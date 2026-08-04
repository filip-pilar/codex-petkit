from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

from PIL import Image

from petkit.contract import load_contract
from petkit.imageops import (
    compare_atlases,
    compose_atlas,
    extract_atlas_frames,
    inspect_frames,
    make_before_after_sheet,
    make_contact_sheet,
    make_standard_filmstrips,
    render_previews,
    validate_atlas,
)
from petkit.project import (
    append_event,
    atomic_write_json,
    current_build_dir,
    load_project,
    next_build_id,
    now_iso,
    save_project,
    sha256_file,
)
from petkit.semantic import make_semantic_recognition_artifacts, validate_design_gate_artifacts
from petkit.v2 import (
    assemble_v2,
    combine_and_validate_blind_reviews,
    make_direction_artifacts,
    validate_direction_semantics,
    validate_semantic_recognition,
    validate_v2,
    validate_visual_qa,
)


BUILD_ALGORITHM_VERSION = 2
RELEASE_BUILD_KIND = "release"
CANDIDATE_BUILD_KIND = "candidate"
ARTIFACT_WORKERS = 4


def _required_look_source(project_dir: Path, project: dict[str, Any], state_id: str) -> Path:
    metadata = project["generation"].get("row_sources", {}).get(state_id)
    relative = metadata.get("path") if isinstance(metadata, dict) else None
    if not isinstance(relative, str):
        raise ValueError(f"V2 build requires an ingested coherent {state_id} source row")
    path = (project_dir / relative).resolve()
    if not path.is_relative_to(project_dir.resolve()) or not path.is_file():
        raise ValueError(f"V2 {state_id} source row is missing or outside the project")
    return path


def _require_v2_generation_gates(project_dir: Path, project: dict[str, Any], contract: Any) -> None:
    look = project.get("look", {})
    if not isinstance(look.get("mechanics"), dict):
        raise ValueError("V2 build requires approved 16-direction look mechanics")
    cardinals = look.get("cardinals")
    if not isinstance(cardinals, dict) or cardinals.get("approved") is not True:
        raise ValueError("V2 build requires an approved cardinal anchor strip")
    if look.get("row_9_approved") is not True:
        raise ValueError("V2 build requires row 9 to pass review before row 10 is assembled")
    validate_design_gate_artifacts(project_dir, contract)


def _standard_alpha_identical(before: Path, after: Path, contract: Any) -> bool:
    with Image.open(before) as opened:
        left = opened.convert("RGBA")
    with Image.open(after) as opened:
        right = opened.convert("RGBA")
    for state in contract.standard_states:
        for column in range(state.frame_count):
            box = (
                column * contract.cell_width,
                state.row * contract.cell_height,
                (column + 1) * contract.cell_width,
                (state.row + 1) * contract.cell_height,
            )
            if left.crop(box).getchannel("A").tobytes() != right.crop(box).getchannel("A").tobytes():
                return False
    return True


def _source_hashes(
    project_dir: Path,
    project: dict[str, Any],
    contract: Any,
    look_sources: dict[str, Path],
) -> dict[str, dict[str, str]]:
    hashes: dict[str, dict[str, str]] = {}
    frames_root = project_dir / "source" / "frames"
    for state in contract.standard_states:
        state_dir = frames_root / state.id
        source_files = state_dir.iterdir() if state_dir.is_dir() else ()
        hashes[state.id] = {
            path.name: sha256_file(path)
            for path in sorted(source_files)
            if path.is_file() and path.suffix.lower() in {".png", ".webp"}
        }
    for state_id, path in look_sources.items():
        hashes[state_id] = {path.name: sha256_file(path)}
    return hashes


def _look_metadata_fingerprint(project: dict[str, Any]) -> str:
    payload = {
        "identity": project.get("identity", {}).get("canonical_sha256"),
        "look": project.get("look", {}),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _build_inputs(project: dict[str, Any]) -> dict[str, Any]:
    return {
        "build_algorithm_version": BUILD_ALGORITHM_VERSION,
        "contract_version": 2,
        "chroma_key": project["generation"]["chroma_key"],
        "chroma_threshold": float(project["generation"]["chroma_threshold"]),
        "look_metadata_fingerprint": _look_metadata_fingerprint(project),
    }


def _changed_source_states(
    current: dict[str, dict[str, str]],
    previous: dict[str, Any] | None,
    contract: Any,
) -> set[str]:
    if not isinstance(previous, dict):
        return {state.id for state in (*contract.standard_states, *contract.look_states)}
    changed: set[str] = set()
    for state in (*contract.standard_states, *contract.look_states):
        if current.get(state.id) != previous.get(state.id):
            changed.add(state.id)
    return changed


def _load_build_record(build_dir: Path) -> dict[str, Any]:
    path = build_dir / "build.json"
    if not path.is_file():
        raise ValueError(f"build record is missing: {path}")
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"build record is not valid JSON: {path}") from exc
    if not isinstance(record, dict):
        raise ValueError(f"build record must be an object: {path}")
    return record


def _copy_complete_directory(
    source: Path,
    destination: Path,
    expected_names: set[str],
    expected_hashes: dict[str, str] | None = None,
) -> bool:
    if not source.is_dir():
        return False
    files = {path.name for path in source.iterdir() if path.is_file()}
    if not expected_names.issubset(files):
        return False
    if expected_hashes is None or any(
        not isinstance(expected_hashes.get(name), str)
        or sha256_file(source / name) != expected_hashes[name]
        for name in expected_names
    ):
        return False
    destination.mkdir(parents=True, exist_ok=True)
    for name in expected_names:
        shutil.copy2(source / name, destination / name)
    return True


def _preflight_edit_scope(
    project: dict[str, Any],
    previous: Path | None,
    previous_record: dict[str, Any] | None,
    current_source_hashes: dict[str, dict[str, str]],
    current_inputs: dict[str, Any],
    contract: Any,
) -> set[str]:
    active_edit = project.get("active_edit")
    if not active_edit:
        return set()
    if previous is None or previous_record is None:
        raise ValueError("an active edit requires an existing comparison build")
    if active_edit.get("comparison_build") != previous.name:
        raise ValueError(
            f"active edit baseline is {active_edit.get('comparison_build')!r}, but current build is {previous.name!r}"
        )
    baseline_hashes = active_edit.get("baseline_source_sha256") or previous_record.get("source_sha256")
    changed = _changed_source_states(current_source_hashes, baseline_hashes, contract)
    baseline_inputs = active_edit.get("baseline_build_inputs") or previous_record.get("build_inputs")
    if isinstance(baseline_inputs, dict):
        if (
            baseline_inputs.get("chroma_key") != current_inputs.get("chroma_key")
            or baseline_inputs.get("chroma_threshold") != current_inputs.get("chroma_threshold")
        ):
            changed.update(state.id for state in contract.states)
    baseline_look = active_edit.get("baseline_look_metadata_fingerprint")
    if isinstance(baseline_look, str) and baseline_look != current_inputs.get("look_metadata_fingerprint"):
        changed.update(state.id for state in contract.look_states)
    if "idle" in changed:
        changed.update(state.id for state in contract.look_states)
    allowed = set(active_edit.get("allowed_states", []))
    unexpected = sorted(changed - allowed)
    if unexpected:
        raise ValueError(
            "edit changed states outside its recorded scope before build: "
            + ", ".join(unexpected)
            + "; include dependent look rows when changing idle frame 0 or look metadata"
        )
    return changed


def _direction_cell_hashes(atlas_path: Path, contract: Any) -> dict[str, str]:
    with Image.open(atlas_path) as opened:
        atlas = opened.convert("RGBA")
    cells: dict[str, str] = {}
    entries = [("neutral", 0, 6)]
    for state in contract.look_states:
        entries.extend(
            (f"{state.id}-{column:02d}", state.row, column)
            for column in range(state.frame_count)
        )
    for label, row, column in entries:
        box = (
            column * contract.cell_width,
            row * contract.cell_height,
            (column + 1) * contract.cell_width,
            (row + 1) * contract.cell_height,
        )
        cells[label] = hashlib.sha256(atlas.crop(box).tobytes()).hexdigest()
    return cells


def _inherit_direction_review(
    project_dir: Path,
    project: dict[str, Any],
    build_id: str,
    parent_build_id: str,
) -> dict[str, Any]:
    build_dir, record = _verify_build_artifact(project_dir, project, build_id)
    parent_dir, parent_record = _verify_build_artifact(project_dir, project, parent_build_id)
    parent_review_dir = project_dir / "reviews" / parent_build_id
    parent_summary_path = parent_review_dir / "review-summary.json"
    if not parent_summary_path.is_file():
        raise ValueError(f"cannot inherit direction review; parent review is missing: {parent_summary_path}")
    parent_summary = json.loads(parent_summary_path.read_text(encoding="utf-8"))
    if not parent_summary.get("ok") or parent_summary.get("atlas_sha256") != parent_record.get("spritesheet_sha256"):
        raise ValueError("cannot inherit a failing or mismatched parent direction review")
    if parent_record.get("build_inputs") != record.get("build_inputs"):
        raise ValueError("cannot inherit direction review after build inputs or look metadata changed")
    current_cells = _direction_cell_hashes(build_dir / "spritesheet.webp", load_contract(2))
    parent_cells = _direction_cell_hashes(parent_dir / "spritesheet.webp", load_contract(2))
    if current_cells != parent_cells:
        raise ValueError("cannot inherit direction review because neutral or look-direction pixels changed")

    review_dir = project_dir / "reviews" / build_id
    review_dir.mkdir(parents=True, exist_ok=False)
    inherited_evidence = review_dir / "inherited-direction"
    inherited_evidence.mkdir()
    for source in parent_review_dir.iterdir():
        if source.name == "review-summary.json":
            continue
        target = inherited_evidence / source.name
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
    summary = {
        **parent_summary,
        "schema_version": 2,
        "reviewed_at": now_iso(),
        "build_id": build_id,
        "atlas_sha256": record["spritesheet_sha256"],
        "direction_review_inherited": True,
        "direction_review_parent_build": parent_build_id,
        "direction_review_parent_atlas_sha256": parent_record["spritesheet_sha256"],
        "direction_review_cell_hashes": current_cells,
    }
    atomic_write_json(review_dir / "review-summary.json", summary)
    return summary


def build_project(project_value: str | Path, *, draft: bool = False) -> dict[str, Any]:
    project_dir, project = load_project(project_value)
    contract = load_contract(2)
    _require_v2_generation_gates(project_dir, project, contract)
    frames_root = project_dir / "source" / "frames"
    look_row_9 = _required_look_source(project_dir, project, "look-a")
    look_row_10 = _required_look_source(project_dir, project, "look-b")
    look_sources = {"look-a": look_row_9, "look-b": look_row_10}
    current_source_hashes = _source_hashes(project_dir, project, contract, look_sources)
    current_inputs = _build_inputs(project)
    previous = current_build_dir(project_dir, project) if project.get("current_build") else None
    previous_record = _load_build_record(previous) if previous is not None else None
    source_changed = (
        _changed_source_states(current_source_hashes, previous_record.get("source_sha256") if previous_record else None, contract)
        if previous is not None
        else set()
    )
    _preflight_edit_scope(
        project,
        previous,
        previous_record,
        current_source_hashes,
        current_inputs,
        contract,
    )
    reusable_parent = (
        previous is not None
        and previous_record is not None
        and previous_record.get("build_algorithm_version") == BUILD_ALGORITHM_VERSION
        and previous_record.get("despill_processing_version") == 2
        and isinstance(previous_record.get("build_inputs"), dict)
        and previous_record["build_inputs"].get("chroma_key") == current_inputs.get("chroma_key")
        and previous_record["build_inputs"].get("chroma_threshold") == current_inputs.get("chroma_threshold")
    )
    build_id = next_build_id(project_dir)
    staging = project_dir / "builds" / f".{build_id}.staging-{uuid.uuid4().hex}"
    final = project_dir / "builds" / build_id
    staging.mkdir(parents=True, exist_ok=False)
    try:
        standard_inspection = inspect_frames(frames_root, contract, standard_only=True)
        atomic_write_json(staging / "standard-frame-inspection.json", standard_inspection)
        if not standard_inspection["ok"]:
            raise ValueError(f"standard frame inspection failed with {len(standard_inspection['errors'])} error(s)")

        standard_atlas = staging / "standard-atlas.png"
        compose_atlas(frames_root, standard_atlas, contract, standard_only=True)
        assembled = assemble_v2(
            base_atlas=standard_atlas,
            look_row_9=look_row_9,
            look_row_10=look_row_10,
            output_dir=staging,
            chroma_key=project["generation"]["chroma_key"],
            chroma_threshold=float(project["generation"]["chroma_threshold"]),
            previous_raw=(previous / "spritesheet-before-despill.png") if reusable_parent and previous is not None else None,
            previous_output=(previous / "spritesheet.png") if reusable_parent and previous is not None else None,
            previous_report=(previous / "despill.json") if reusable_parent and previous is not None else None,
        )
        spritesheet = Path(assembled["webp"])
        validation = validate_v2(
            spritesheet,
            staging / "validation.json",
            chroma_key=project["generation"]["chroma_key"],
        )
        validation["file"] = "spritesheet.webp"
        atomic_write_json(staging / "validation.json", validation)
        if not validation["ok"]:
            raise ValueError(f"V2 atlas validation failed with {len(validation['errors'])} error(s)")

        local_validation = validate_atlas(spritesheet, contract)
        local_validation["path"] = "spritesheet.webp"
        atomic_write_json(staging / "local-validation.json", local_validation)
        if not local_validation["ok"]:
            raise ValueError(f"local V2 validation failed with {len(local_validation['errors'])} error(s)")

        registered_frames = staging / "registered-frames"
        extract_atlas_frames(spritesheet, registered_frames, contract)
        frame_inspection = inspect_frames(registered_frames, contract)
        atomic_write_json(staging / "frame-inspection.json", frame_inspection)
        if not frame_inspection["ok"]:
            raise ValueError(f"registered frame inspection failed with {len(frame_inspection['errors'])} error(s)")

        make_contact_sheet(spritesheet, staging / "contact-sheet.png", contract)
        all_preview_ids = {state.id for state in contract.states}
        all_standard_ids = {state.id for state in contract.standard_states}
        render_ids = all_preview_ids
        filmstrip_ids = all_standard_ids
        reused_preview_ids: set[str] = set()
        reused_filmstrip_ids: set[str] = set()
        if reusable_parent and previous is not None:
            parent_artifacts = previous_record.get("artifact_sha256", {}) if previous_record else {}
            parent_preview_hashes = parent_artifacts.get("previews") if isinstance(parent_artifacts, dict) else None
            parent_filmstrip_hashes = parent_artifacts.get("standard_filmstrips") if isinstance(parent_artifacts, dict) else None
            look_dirty = bool(source_changed & {"look-a", "look-b", "idle"})
            render_dirty = set(source_changed) | ({"look-a", "look-b"} if look_dirty else set())
            render_dirty &= all_preview_ids
            filmstrip_dirty = set(source_changed) & all_standard_ids
            if _copy_complete_directory(
                previous / "previews",
                staging / "previews",
                {f"{state_id}.gif" for state_id in all_preview_ids - render_dirty},
                parent_preview_hashes if isinstance(parent_preview_hashes, dict) else None,
            ):
                reused_preview_ids = all_preview_ids - render_dirty
                render_ids = render_dirty
            if _copy_complete_directory(
                previous / "qa" / "standard-filmstrips",
                staging / "qa" / "standard-filmstrips",
                {f"{state_id}.png" for state_id in all_standard_ids - filmstrip_dirty},
                parent_filmstrip_hashes if isinstance(parent_filmstrip_hashes, dict) else None,
            ):
                reused_filmstrip_ids = all_standard_ids - filmstrip_dirty
                filmstrip_ids = filmstrip_dirty
        spritesheet_hash = sha256_file(spritesheet)
        artifact_results: dict[str, Any] = {}
        with ThreadPoolExecutor(max_workers=ARTIFACT_WORKERS, thread_name_prefix="petkit-artifact") as workers:
            futures = {
                "previews": workers.submit(
                    render_previews,
                    registered_frames,
                    staging / "previews",
                    contract,
                    state_ids=render_ids,
                ),
                "filmstrips": workers.submit(
                    make_standard_filmstrips,
                    registered_frames,
                    staging / "qa" / "standard-filmstrips",
                    contract,
                    state_ids=filmstrip_ids,
                ),
            }
            if not draft:
                futures["direction"] = workers.submit(
                    make_direction_artifacts,
                    spritesheet,
                    staging / "qa",
                    staging / "qa-private",
                )
                futures["semantic"] = workers.submit(
                    make_semantic_recognition_artifacts,
                    registered_frames,
                    staging / "qa" / "semantic-recognition",
                    staging / "qa-private",
                    contract,
                    spritesheet_hash,
                )
            for name, future in futures.items():
                artifact_results[name] = future.result()
        if draft:
            direction_artifacts: dict[str, str] = {}
            semantic_artifacts: dict[str, str] = {}
        else:
            direction_artifacts = artifact_results["direction"]
            semantic_artifacts = artifact_results["semantic"]
        manifest = {
            "id": project["id"],
            "displayName": project["display_name"],
            "description": project["description"],
            "spriteVersionNumber": 2,
            "spritesheetPath": "spritesheet.webp",
        }
        atomic_write_json(staging / "pet.json", manifest)

        change_report: dict[str, Any] = {
            "ok": True,
            "first_build": True,
            "changed_states": {},
            "unchanged_states": [],
            "added_states": ["look-a", "look-b"],
            "removed_states": [],
            "source_changed_states": sorted(source_changed),
        }
        if previous is not None:
            change_report = compare_atlases(previous / "spritesheet.webp", spritesheet, contract)
            change_report["first_build"] = False
            change_report["before"] = f"../{previous.name}/spritesheet.webp"
            change_report["after"] = "spritesheet.webp"
            change_report["source_changed_states"] = sorted(source_changed)
            make_before_after_sheet(previous / "contact-sheet.png", staging / "contact-sheet.png", staging / "before-after.png")
            with Image.open(previous / "spritesheet.webp") as opened:
                previous_is_standard_only = opened.height == contract.standard_rows * contract.cell_height
            if previous_is_standard_only:
                changed_standard = sorted(set(change_report["changed_states"]) & {state.id for state in contract.standard_states})
                alpha_preserved = _standard_alpha_identical(
                    previous / "spritesheet.webp",
                    spritesheet,
                    contract,
                )
                if not alpha_preserved:
                    raise ValueError("V2 upgrade changed standard-row alpha geometry")
                change_report["v2_upgrade"] = {
                    "standard_states_with_edge_rgb_cleanup": changed_standard,
                    "alpha_geometry_preserved": True,
                    "reason": "one final edge-local chroma despill pass; standard animation source art and alpha masks are unchanged",
                }

        active_edit = project.get("active_edit")
        if active_edit:
            allowed = set(active_edit.get("allowed_states", []))
            changed = set(change_report["changed_states"]) | set(change_report["added_states"])
            unexpected = sorted(changed - allowed)
            change_report["edit_scope"] = {
                "edit_id": active_edit.get("edit_id"),
                "allowed_states": sorted(allowed),
                "unexpected_states": unexpected,
                "scope_ok": not unexpected,
            }
            if unexpected:
                raise ValueError(f"edit changed states outside its recorded scope: {', '.join(unexpected)}")
        atomic_write_json(staging / "change-report.json", change_report)

        artifact_hashes = {
            "previews": {
                path.name: sha256_file(path)
                for path in sorted((staging / "previews").glob("*.gif"))
            },
            "standard_filmstrips": {
                path.name: sha256_file(path)
                for path in sorted((staging / "qa" / "standard-filmstrips").glob("*.png"))
            },
        }

        build_record = {
            "schema_version": 2,
            "build_id": build_id,
            "pet_id": project["id"],
            "contract_version": 2,
            "build_kind": CANDIDATE_BUILD_KIND if draft else RELEASE_BUILD_KIND,
            "build_algorithm_version": BUILD_ALGORITHM_VERSION,
            "despill_processing_version": 2,
            "created_at": now_iso(),
            "previous_build": project.get("current_build"),
            "source_sha256": current_source_hashes,
            "build_inputs": current_inputs,
            "spritesheet_sha256": spritesheet_hash,
            "validation": "validation.json",
            "local_validation": "local-validation.json",
            "standard_frame_inspection": "standard-frame-inspection.json",
            "frame_inspection": "frame-inspection.json",
            "registration": "look-registration.json",
            "despill": "despill.json",
            "contact_sheet": "contact-sheet.png",
            "previews": "previews",
            "standard_filmstrips": "qa/standard-filmstrips",
            "direction_qa": {key: str(Path(value).relative_to(staging)) for key, value in direction_artifacts.items()},
            "semantic_qa": {key: str(Path(value).relative_to(staging)) for key, value in semantic_artifacts.items()},
            "change_report": "change-report.json",
            "before_after": "before-after.png" if previous is not None else None,
            "edit_id": active_edit.get("edit_id") if active_edit else None,
            "artifact_reuse": {
                "parent_build": previous.name if reusable_parent and previous is not None else None,
                "preview_states": sorted(reused_preview_ids),
                "standard_filmstrips": sorted(reused_filmstrip_ids),
                "candidate": draft,
            },
            "artifact_sha256": artifact_hashes,
        }
        atomic_write_json(staging / "build.json", build_record)
        os.replace(staging, final)
        if draft:
            append_event(
                project_dir,
                "candidate-build-created-v2",
                {
                    "build_id": build_id,
                    "spritesheet_sha256": build_record["spritesheet_sha256"],
                    "baseline_build": build_record["previous_build"],
                },
            )
        else:
            project["current_build"] = build_id
            project["status"] = "review"
            if active_edit:
                project["active_edit"]["comparison_build"] = build_id
                project["active_edit"]["latest_build"] = build_id
            save_project(project_dir, project)
            append_event(
                project_dir,
                "build-created-v2",
                {"build_id": build_id, "spritesheet_sha256": build_record["spritesheet_sha256"], "previous_build": build_record["previous_build"]},
            )
        return {
            "ok": True,
            "project": str(project_dir),
            "build_id": build_id,
            "build_dir": str(final),
            "build_kind": build_record["build_kind"],
            "validation": {**validation, "file": str(final / "spritesheet.webp")},
            "frame_inspection": frame_inspection,
            "change_report": change_report,
            "direction_qa": {key: str(final / Path(value).relative_to(staging)) for key, value in direction_artifacts.items()},
            "semantic_qa": {key: str(final / Path(value).relative_to(staging)) for key, value in semantic_artifacts.items()},
        }
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _build_dir(project_dir: Path, build_id: Any) -> Path:
    if not isinstance(build_id, str) or not re.fullmatch(r"build-\d{4}", build_id):
        raise ValueError(f"invalid build id: {build_id!r}")
    return project_dir / "builds" / build_id


def _verify_build_artifact(project_dir: Path, project: dict[str, Any], build_id: str) -> tuple[Path, dict[str, Any]]:
    build_dir = _build_dir(project_dir, build_id)
    build_record_path = build_dir / "build.json"
    spritesheet = build_dir / "spritesheet.webp"
    if not build_dir.is_dir() or not build_record_path.is_file() or not spritesheet.is_file():
        raise ValueError(f"build is incomplete: {build_dir}")
    build_record = json.loads(build_record_path.read_text(encoding="utf-8"))
    if build_record.get("build_id") != build_id or build_record.get("pet_id") != project["id"]:
        raise ValueError("build record identity does not match the selected project and build")
    if build_record.get("contract_version") != 2:
        raise ValueError("only V2 builds are supported")
    if build_record.get("build_kind", RELEASE_BUILD_KIND) != RELEASE_BUILD_KIND:
        raise ValueError("candidate builds cannot be reviewed, accepted, or installed")
    expected_hash = build_record.get("spritesheet_sha256")
    if not isinstance(expected_hash, str) or sha256_file(spritesheet) != expected_hash:
        raise ValueError("build spritesheet no longer matches its immutable build record")
    fresh = validate_atlas(spritesheet, load_contract(2))
    if not fresh.get("ok"):
        raise ValueError("build spritesheet no longer passes deterministic V2 validation")
    manifest = json.loads((build_dir / "pet.json").read_text(encoding="utf-8"))
    if manifest.get("spriteVersionNumber") != 2:
        raise ValueError("build manifest is not a V2 pet manifest")
    return build_dir, build_record


def review_directions(
    project_value: str | Path,
    build_id: str,
    *,
    direction_semantics: Path | None,
    blind_verdicts: list[Path],
    semantic_verdicts: list[Path],
    independent_visual_qas: list[Path],
    continuity_override_note: str = "",
    inherit_direction_from: str | None = None,
) -> dict[str, Any]:
    project_dir, project = load_project(project_value)
    build_dir, record = _verify_build_artifact(project_dir, project, build_id)
    atlas_hash = record["spritesheet_sha256"]
    inherited_direction: dict[str, Any] | None = None
    if inherit_direction_from:
        if direction_semantics is not None or blind_verdicts:
            raise ValueError("direction review inheritance cannot be combined with fresh direction verdicts")
        inherited_direction = _inherit_direction_review(
            project_dir,
            project,
            build_id,
            inherit_direction_from,
        )
    else:
        if direction_semantics is None or len(blind_verdicts) < 3 or len(blind_verdicts) % 2 == 0:
            raise ValueError("fresh direction review requires semantics and an odd number of at least three blind verdicts")
        semantics = json.loads(direction_semantics.read_text(encoding="utf-8"))
        validate_direction_semantics(semantics, load_contract(2), atlas_hash)
    semantic_answer_key_path = build_dir / "qa-private" / "semantic-recognition-answer-key.json"
    if len(semantic_verdicts) != 3:
        raise ValueError("V2 review requires exactly three independent semantic recognition verdicts")
    if not semantic_answer_key_path.is_file():
        raise ValueError("V2 build is missing the private semantic-recognition answer key")
    semantic_answer_key = json.loads(semantic_answer_key_path.read_text(encoding="utf-8"))
    semantic_reviewer_ids: list[str] = []
    for source in semantic_verdicts:
        semantic = json.loads(source.read_text(encoding="utf-8"))
        validate_semantic_recognition(semantic, semantic_answer_key, atlas_hash)
        semantic_reviewer_ids.append(semantic["reviewer_id"].strip())
    if len(set(semantic_reviewer_ids)) != len(semantic_reviewer_ids):
        raise ValueError("V2 semantic review requires three distinct reviewer identifiers")
    if len(independent_visual_qas) != 3:
        raise ValueError("V2 review requires exactly three independent visual QA verdicts")
    visuals = []
    visual_reviewer_ids: list[str] = []
    for source in independent_visual_qas:
        visual = json.loads(source.read_text(encoding="utf-8"))
        validate_visual_qa(visual, atlas_hash)
        visuals.append(visual)
        visual_reviewer_ids.append(visual["reviewer_id"].strip())
    if len(set(visual_reviewer_ids)) != len(visual_reviewer_ids):
        raise ValueError("V2 visual review requires three distinct reviewer identifiers")
    continuity = json.loads((build_dir / "qa" / "direction-continuity.json").read_text(encoding="utf-8"))
    if inherited_direction and not continuity_override_note.strip():
        inherited_note = inherited_direction.get("continuity_override_note")
        if isinstance(inherited_note, str):
            continuity_override_note = inherited_note
    if continuity.get("reviewRequired") and not continuity_override_note.strip():
        raise ValueError("direction continuity warnings require an explicit review/override note")
    review_dir = project_dir / "reviews" / build_id
    if inherited_direction is None:
        review_dir.mkdir(parents=True, exist_ok=True)
        blind = combine_and_validate_blind_reviews(
            answer_key=build_dir / "qa-private" / "direction-blind-answer-key.json",
            verdicts=blind_verdicts,
            output_dir=review_dir,
        )
        assert direction_semantics is not None
        shutil.copy2(direction_semantics, review_dir / "direction-semantics.json")
    else:
        blind = {"validation": {"ok": bool(inherited_direction.get("blind_majority_ok"))}}
    for index, source in enumerate(independent_visual_qas, start=1):
        shutil.copy2(source, review_dir / f"independent-visual-qa-{index:02d}.json")
    for index, source in enumerate(semantic_verdicts, start=1):
        shutil.copy2(source, review_dir / f"semantic-recognition-{index:02d}.json")
    summary = {
        "schema_version": 2,
        "reviewed_at": now_iso(),
        "build_id": build_id,
        "atlas_sha256": atlas_hash,
        "direction_semantics_ok": bool(inherited_direction.get("direction_semantics_ok")) if inherited_direction else True,
        "blind_majority_ok": bool(inherited_direction.get("blind_majority_ok")) if inherited_direction else bool(blind["validation"].get("ok")),
        "independent_visual_qa_ok": True,
        "independent_visual_qa_count": len(visuals),
        "independent_visual_qa_unanimous": True,
        "semantic_recognition_ok": True,
        "semantic_recognition_count": len(semantic_verdicts),
        "semantic_recognition_unanimous": True,
        "continuity_review_required": bool(continuity.get("reviewRequired")),
        "continuity_override_note": continuity_override_note.strip() or None,
        "direction_review_inherited": bool(inherited_direction),
        "direction_review_parent_build": inherit_direction_from if inherited_direction else None,
        "direction_review_parent_atlas_sha256": inherited_direction.get("direction_review_parent_atlas_sha256") if inherited_direction else None,
        "direction_review_cell_hashes": inherited_direction.get("direction_review_cell_hashes") if inherited_direction else None,
        "ok": True,
    }
    atomic_write_json(review_dir / "review-summary.json", summary)
    append_event(project_dir, "direction-review-completed", {"build_id": build_id, "atlas_sha256": atlas_hash})
    return {"ok": True, "project": str(project_dir), "review_dir": str(review_dir), **summary}


def accept_build(
    project_value: str | Path,
    build_id: str | None = None,
    *,
    confirm_visual_qa: bool = False,
    review_note: str = "",
) -> dict[str, Any]:
    project_dir, project = load_project(project_value)
    selected = build_id or project.get("current_build")
    if not selected:
        raise ValueError("project has no build to accept")
    build_dir, record = _verify_build_artifact(project_dir, project, selected)
    review_path = project_dir / "reviews" / selected / "review-summary.json"
    if not review_path.is_file():
        raise ValueError("V2 acceptance requires completed direction review")
    direction_review = json.loads(review_path.read_text(encoding="utf-8"))
    if not direction_review.get("ok") or direction_review.get("atlas_sha256") != record["spritesheet_sha256"]:
        raise ValueError("V2 direction review is failing or belongs to another atlas")
    for report_name in ("validation.json", "frame-inspection.json"):
        report = json.loads((build_dir / report_name).read_text(encoding="utf-8"))
        if not report.get("ok"):
            raise ValueError(f"cannot accept a build with failing {report_name}")
    if not confirm_visual_qa:
        raise ValueError("acceptance requires explicit visual QA confirmation")
    if not review_note.strip():
        raise ValueError("acceptance requires a concise visual QA review note")
    active_edit = project.get("active_edit")
    project["current_build"] = selected
    project["accepted_build"] = selected
    project["status"] = "accepted"
    if active_edit and active_edit.get("latest_build") == selected:
        project["active_edit"] = None
    save_project(project_dir, project)
    acceptance = {
        "schema_version": 2,
        "accepted_at": now_iso(),
        "build_id": selected,
        "visual_qa_confirmed": True,
        "direction_review": str(review_path.relative_to(project_dir)),
        "review_note": review_note.strip(),
        "edit_id": active_edit.get("edit_id") if active_edit else None,
    }
    atomic_write_json(project_dir / "history" / f"acceptance-{selected}.json", acceptance)
    append_event(project_dir, "build-accepted-v2", acceptance)
    return {"ok": True, "project": str(project_dir), **acceptance}


def _copy_package(build_dir: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    for name in ("pet.json", "spritesheet.webp"):
        source = build_dir / name
        if not source.is_file():
            raise ValueError(f"build is missing package file: {source}")
        shutil.copy2(source, destination / name)


def install_build(project_value: str | Path, target_root: Path, build_id: str | None = None) -> dict[str, Any]:
    project_dir, project = load_project(project_value)
    selected = build_id or project.get("accepted_build")
    if not selected:
        raise ValueError("project has no accepted build to install")
    if selected != project.get("accepted_build"):
        raise ValueError("only the project's accepted build may be installed")
    build_dir, _ = _verify_build_artifact(project_dir, project, selected)
    target_root = target_root.expanduser().resolve()
    target_root.mkdir(parents=True, exist_ok=True)
    target = target_root / project["id"]
    staging = target_root / f".{project['id']}.install-{uuid.uuid4().hex}"
    displaced = target_root / f".{project['id']}.previous-{uuid.uuid4().hex}"
    backup_dir: Path | None = None
    try:
        _copy_package(build_dir, staging)
        if target.exists():
            timestamp = now_iso().replace(":", "").replace("-", "") + f"-{uuid.uuid4().hex[:8]}"
            backup_dir = project_dir / "backups" / "installed" / timestamp
            shutil.copytree(target, backup_dir)
            os.replace(target, displaced)
        os.replace(staging, target)
        if displaced.exists():
            shutil.rmtree(displaced)
    except Exception:
        if target.exists() and displaced.exists():
            shutil.rmtree(target, ignore_errors=True)
        if displaced.exists() and not target.exists():
            os.replace(displaced, target)
        shutil.rmtree(staging, ignore_errors=True)
        raise
    install_record = {
        "schema_version": 2,
        "installed_at": now_iso(),
        "build_id": selected,
        "target": str(target),
        "backup": str(backup_dir) if backup_dir else None,
        "spritesheet_sha256": sha256_file(target / "spritesheet.webp"),
    }
    atomic_write_json(project_dir / "history" / "last-install.json", install_record)
    append_event(project_dir, "build-installed-v2", install_record)
    return {"ok": True, **install_record}


def rollback_install(project_value: str | Path, target_root: Path, backup: Path | None = None) -> dict[str, Any]:
    project_dir, project = load_project(project_value)
    backup_root = project_dir / "backups" / "installed"
    if backup is None:
        candidates = sorted(path for path in backup_root.iterdir() if path.is_dir()) if backup_root.is_dir() else []
        if not candidates:
            raise ValueError(f"no installed-package backup exists for {project['id']}")
        backup = candidates[-1]
    backup = backup.expanduser().resolve()
    if not backup.is_relative_to(backup_root.resolve()):
        raise ValueError(f"backup must be inside this project's installed backups: {backup_root}")
    for name in ("pet.json", "spritesheet.webp"):
        if not (backup / name).is_file():
            raise ValueError(f"backup is incomplete: {backup}")
    target_root = target_root.expanduser().resolve()
    target_root.mkdir(parents=True, exist_ok=True)
    target = target_root / project["id"]
    staging = target_root / f".{project['id']}.rollback-{uuid.uuid4().hex}"
    displaced = target_root / f".{project['id']}.pre-rollback-{uuid.uuid4().hex}"
    displaced_backup: Path | None = None
    try:
        shutil.copytree(backup, staging)
        if target.exists():
            timestamp = now_iso().replace(":", "").replace("-", "") + f"-{uuid.uuid4().hex[:8]}-pre-rollback"
            displaced_backup = backup_root / timestamp
            shutil.copytree(target, displaced_backup)
            os.replace(target, displaced)
        os.replace(staging, target)
        if displaced.exists():
            shutil.rmtree(displaced)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        if displaced.exists() and not target.exists():
            os.replace(displaced, target)
        raise
    record = {
        "schema_version": 2,
        "rolled_back_at": now_iso(),
        "target": str(target),
        "restored_backup": str(backup),
        "displaced_backup": str(displaced_backup) if displaced_backup else None,
        "spritesheet_sha256": sha256_file(target / "spritesheet.webp"),
    }
    atomic_write_json(project_dir / "history" / "last-rollback.json", record)
    append_event(project_dir, "install-rolled-back", record)
    return {"ok": True, **record}
