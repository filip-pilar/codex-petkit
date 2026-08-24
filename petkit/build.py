from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Iterator

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
    copy_tree_without_symlinks,
    current_build_dir,
    ensure_tree_has_no_symlinks,
    file_writer_lock,
    fork_build_parameters,
    json_sha256,
    load_project,
    next_build_id,
    now_iso,
    operation_marker_path,
    paths_overlap,
    read_json,
    recover_operation_path,
    remove_operation_marker,
    restore_project_snapshot_for_recovery,
    recorded_authority_values,
    project_mutation,
    safe_project_directory,
    save_project,
    sha256_file,
    source_image_files,
    source_file_snapshot,
    transaction_trace,
    write_operation_marker,
    IDENTITY_FILE,
    PROJECT_FILE,
    TransactionRecoveryError,
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
DESPILL_CACHE_FILES = (
    "spritesheet-before-despill.png",
    "spritesheet.png",
    "despill.json",
)
REVIEW_AUTHORITY_FILES = (
    "qa-private/direction-blind-answer-key.json",
    "qa-private/semantic-recognition-answer-key.json",
    "qa/direction-continuity.json",
)
PACKAGE_FILES = ("pet.json", "spritesheet.webp")
BACKUP_SIDECAR = ".petkit-backup.json"


def _json_sha256(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _relative_file_hashes(root: Path, names: tuple[str, ...]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for name in names:
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"required file is missing or symbolic: {path}")
        hashes[name] = sha256_file(path)
    return hashes


def _hash_manifest_matches(root: Path, recorded: Any, names: tuple[str, ...]) -> bool:
    if not isinstance(recorded, dict) or set(recorded) != set(names):
        return False
    try:
        return all(
            isinstance(recorded.get(name), str)
            and re.fullmatch(r"[0-9a-f]{64}", recorded[name]) is not None
            and not (root / name).is_symlink()
            and (root / name).is_file()
            and sha256_file(root / name) == recorded[name]
            for name in names
        )
    except OSError:
        return False


def _verify_hash_manifest(root: Path, recorded: Any, names: tuple[str, ...], label: str) -> None:
    if not _hash_manifest_matches(root, recorded, names):
        raise ValueError(f"{label} no longer matches its immutable build record")


def recorded_project_file(
    project_dir: Path,
    metadata: Any,
    *,
    label: str,
    path_field: str = "path",
    hash_field: str = "sha256",
) -> tuple[Path, str]:
    if not isinstance(metadata, dict):
        raise ValueError(f"{label} metadata is missing")
    relative = metadata.get(path_field)
    expected_hash = metadata.get(hash_field)
    if not isinstance(relative, str) or not relative:
        raise ValueError(f"{label} path is missing")
    if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise ValueError(f"{label} recorded SHA-256 is missing or invalid")
    lexical_path = project_dir / relative
    current = project_dir.resolve()
    try:
        relative_parts = lexical_path.relative_to(project_dir).parts
    except ValueError as exc:
        raise ValueError(f"{label} is outside the project") from exc
    for part in relative_parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{label} must not use symbolic links")
    path = lexical_path.resolve()
    if not path.is_relative_to(project_dir.resolve()) or not path.is_file():
        raise ValueError(f"{label} is missing or outside the project")
    actual_hash = sha256_file(path)
    if actual_hash != expected_hash:
        raise ValueError(f"{label} no longer matches its recorded SHA-256")
    return path, actual_hash


def _canonical_identity_hash(project_dir: Path, project: dict[str, Any]) -> str:
    identity = project.get("identity")
    if not isinstance(identity, dict) or identity.get("approved") is not True:
        raise ValueError("canonical identity is not approved")
    _path, actual_hash = recorded_project_file(
        project_dir,
        identity,
        label="canonical identity",
        path_field="canonical_reference",
        hash_field="canonical_sha256",
    )
    return actual_hash


def _look_basis_components(project_dir: Path, project: dict[str, Any]) -> dict[str, str]:
    look = project.get("look")
    if not isinstance(look, dict):
        raise ValueError("V2 look metadata is missing")
    identity_hash = _canonical_identity_hash(project_dir, project)
    _mechanics_path, mechanics_hash = recorded_project_file(
        project_dir,
        look.get("mechanics"),
        label="look mechanics",
    )
    cardinals = look.get("cardinals")
    if not isinstance(cardinals, dict) or cardinals.get("approved") is not True:
        raise ValueError("V2 build requires an approved cardinal anchor strip")
    _cardinals_path, cardinals_hash = recorded_project_file(
        project_dir,
        cardinals,
        label="cardinal anchor strip",
    )
    _look_a_path, look_a_hash = recorded_project_file(
        project_dir,
        project.get("generation", {}).get("row_sources", {}).get("look-a"),
        label="look-a source row",
    )
    return {
        "canonical_identity_sha256": identity_hash,
        "mechanics_sha256": mechanics_hash,
        "cardinals_sha256": cardinals_hash,
        "look_a_sha256": look_a_hash,
    }


def look_basis_fingerprint(project_dir: Path, project: dict[str, Any]) -> str:
    """Return the narrow identity/mechanics/cardinals/look-a authority fingerprint."""

    return _json_sha256(_look_basis_components(project_dir, project))


def authority_snapshot(project_dir: Path, project: dict[str, Any]) -> dict[str, str]:
    """Resolve and verify only the canonical files that authorize a V2 build."""

    components = _look_basis_components(project_dir, project)
    look = project.get("look", {})
    approval = look.get("row_9_approval") if isinstance(look, dict) else None
    if look.get("row_9_approved") is not True or not isinstance(approval, dict):
        raise ValueError("V2 build requires row 9 to pass review before row 10 is assembled")
    basis_hash = _json_sha256(components)
    if approval.get("row_sha256") != components["look_a_sha256"] or approval.get("basis_sha256") != basis_hash:
        raise ValueError("row 9 approval is stale for the current identity, mechanics, cardinals, or look-a row")
    look_b_metadata = project.get("generation", {}).get("row_sources", {}).get("look-b")
    _look_b_path, look_b_hash = recorded_project_file(
        project_dir,
        look_b_metadata,
        label="look-b source row",
    )
    if not isinstance(look_b_metadata, dict) or look_b_metadata.get("row_9_basis_sha256") != basis_hash:
        raise ValueError("look-b source row is stale for the approved row 9 basis")
    return {
        **components,
        "look_b_sha256": look_b_hash,
        "row_9_basis_sha256": basis_hash,
    }


def _required_look_source(project_dir: Path, project: dict[str, Any], state_id: str) -> Path:
    metadata = project["generation"].get("row_sources", {}).get(state_id)
    path, _actual_hash = recorded_project_file(project_dir, metadata, label=f"{state_id} source row")
    return path


def _verify_variant_fork_snapshot(
    project_dir: Path,
    project: dict[str, Any],
) -> None:
    if not project.get("parent_id") or project.get("accepted_build"):
        return
    generation = project.get("generation")
    snapshot = generation.get("fork_snapshot") if isinstance(generation, dict) else None
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != 2:
        raise ValueError(
            "variant is missing its fork snapshot; run upgrade-project to explicitly rebaseline "
            "the current legacy variant state"
        )
    if snapshot.get("source_sha256") != source_file_snapshot(project_dir):
        raise ValueError("variant source changed before its first child-local baseline build")
    if snapshot.get("authority") != recorded_authority_values(project):
        raise ValueError("variant authority changed before its first child-local baseline build")
    if snapshot.get("build_parameters") != fork_build_parameters(project):
        raise ValueError("variant chroma parameters changed before its first child-local baseline build")


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
    ensure_tree_has_no_symlinks(project_dir / "source", boundary=project_dir)
    for state in contract.standard_states:
        state_dir = frames_root / state.id
        hashes[state.id] = {
            path.name: sha256_file(path)
            for path in source_image_files(state_dir, boundary=project_dir)
        }
    for state_id, path in look_sources.items():
        if path.is_symlink() or not path.resolve().is_relative_to(project_dir.resolve()):
            raise ValueError(f"look source must be a regular project file: {path}")
        hashes[state_id] = {path.name: sha256_file(path)}
    return hashes


def _build_inputs(project: dict[str, Any], authority: dict[str, str]) -> dict[str, Any]:
    return {
        "build_algorithm_version": BUILD_ALGORITHM_VERSION,
        "contract_version": 2,
        "chroma_key": project["generation"]["chroma_key"],
        "chroma_threshold": float(project["generation"]["chroma_threshold"]),
        "authority": authority,
        "authority_fingerprint": _json_sha256(authority),
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
    if source.is_symlink() or not source.is_dir():
        return False
    files = {
        path.name
        for path in source.iterdir()
        if not path.is_symlink() and path.is_file()
    }
    if not expected_names.issubset(files):
        return False
    if expected_hashes is None:
        return False
    destination.mkdir(parents=True, exist_ok=False)
    try:
        for name in sorted(expected_names):
            expected = expected_hashes.get(name)
            source_path = source / name
            target = destination / name
            if (
                not isinstance(expected, str)
                or not re.fullmatch(r"[0-9a-f]{64}", expected)
                or source_path.is_symlink()
                or not source_path.is_file()
            ):
                raise ValueError("parent artifact is missing, symbolic, or lacks an authoritative hash")
            shutil.copy2(source_path, target)
            if target.is_symlink() or sha256_file(target) != expected:
                raise ValueError("copied parent artifact does not match its authoritative hash")
        return True
    except (OSError, ValueError):
        shutil.rmtree(destination)
        return False


def _copy_parent_atlas_snapshot(
    previous: Path,
    previous_record: dict[str, Any],
    destination: Path,
) -> Path:
    expected = previous_record.get("spritesheet_sha256")
    source = previous / "spritesheet.webp"
    if (
        not isinstance(expected, str)
        or not re.fullmatch(r"[0-9a-f]{64}", expected)
        or source.is_symlink()
        or not source.is_file()
    ):
        raise ValueError("parent atlas is missing, symbolic, or lacks an authoritative hash")
    destination.parent.mkdir(parents=True, exist_ok=False)
    shutil.copy2(source, destination)
    if destination.is_symlink() or sha256_file(destination) != expected:
        raise ValueError("copied parent atlas no longer matches its immutable build record")
    return destination


def _cleanup_private_snapshot(path: Path, label: str, recovery_path: Path) -> list[str]:
    return recover_operation_path(path, label, quarantine=recovery_path)


def _operation_build_is_verified(
    project_dir: Path,
    project: dict[str, Any],
    build_id: str,
    final: Path,
    expected_record: dict[str, Any] | None,
    *,
    draft: bool,
) -> bool:
    if expected_record is None or final.is_symlink() or not final.is_dir():
        return False
    try:
        durable_record = read_json(final / "build.json")
        if durable_record != expected_record:
            return False
        if draft:
            if (
                durable_record.get("build_id") != build_id
                or durable_record.get("pet_id") != project["id"]
                or durable_record.get("build_kind") != CANDIDATE_BUILD_KIND
                or durable_record.get("contract_version") != 2
            ):
                return False
            _verify_installable_package(final, project["id"], durable_record)
            return bool(validate_atlas(final / "spritesheet.webp", load_contract(2)).get("ok"))
        _verified_dir, verified_record = _verify_build_artifact(project_dir, project, build_id)
        return verified_record == expected_record
    except (OSError, ValueError):
        return False


def _candidate_event_is_durable(project_dir: Path, expected: dict[str, Any]) -> bool:
    events_path = project_dir / "history" / "events.jsonl"
    if events_path.is_symlink() or not events_path.is_file():
        return False
    try:
        for line in events_path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                isinstance(record, dict)
                and record.get("event") == "candidate-build-created-v2"
                and record.get("build_id") == expected.get("build_id")
                and record.get("spritesheet_sha256") == expected.get("spritesheet_sha256")
                and record.get("baseline_build") == expected.get("baseline_build")
            ):
                return True
    except OSError:
        return False
    return False


def _reconcile_build_abnormal_exit(
    project_dir: Path,
    original_project: dict[str, Any],
    build_id: str,
    staging: Path,
    final: Path,
    input_snapshot: Path,
    input_recovery: Path,
    staging_recovery: Path,
    final_recovery: Path,
    expected_record: dict[str, Any] | None,
    candidate_record_path: Path,
    expected_candidate_record: dict[str, Any] | None,
    *,
    draft: bool,
    operation_id: str,
) -> tuple[bool, list[str]]:
    """Reconcile one interrupted build from durable metadata and operation identities."""

    errors: list[str] = []
    durable_project: dict[str, Any] | None = None
    try:
        _path, durable_project = load_project(project_dir)
    except BaseException:
        errors.extend(
            restore_project_snapshot_for_recovery(
                project_dir,
                original_project,
                "project metadata after interrupted build",
                operation_id=f"build-recovery-{operation_id}",
            )
        )
        if not errors:
            durable_project = original_project

    final_exists = os.path.lexists(final)
    final_verified = bool(
        final_exists
        and durable_project is not None
        and _operation_build_is_verified(
            project_dir,
            durable_project,
            build_id,
            final,
            expected_record,
            draft=draft,
        )
    )
    pointer_matches = bool(not draft and durable_project is not None and durable_project.get("current_build") == build_id)
    candidate_record_matches = False
    candidate_event_matches = False
    if draft and expected_candidate_record is not None:
        try:
            candidate_record_matches = read_json(candidate_record_path) == expected_candidate_record
        except (OSError, ValueError):
            candidate_record_matches = False
        candidate_event_matches = _candidate_event_is_durable(project_dir, expected_candidate_record)
    durable_commit = bool(
        final_verified
        and ((not draft and pointer_matches) or (draft and (candidate_record_matches or candidate_event_matches)))
    )

    if draft and durable_commit and candidate_event_matches and not candidate_record_matches:
        try:
            atomic_write_json(
                candidate_record_path,
                expected_candidate_record,
                operation_id=f"candidate-recovery-{operation_id}",
            )
        except BaseException as recovery_error:
            errors.append(
                f"durable candidate history could not be reconstructed at {candidate_record_path}: "
                f"{type(recovery_error).__name__}: {recovery_error}"
            )

    if pointer_matches and not durable_commit:
        metadata_errors = restore_project_snapshot_for_recovery(
            project_dir,
            original_project,
            "project metadata pointing at an unverified interrupted build",
            operation_id=f"build-recovery-{operation_id}",
        )
        errors.extend(metadata_errors)
        if not metadata_errors:
            durable_project = original_project

    if final_exists and not durable_commit:
        operation_owned = False
        if expected_record is not None:
            try:
                operation_owned = read_json(final / "build.json") == expected_record
            except (OSError, ValueError):
                operation_owned = True
        if operation_owned:
            errors.extend(
                recover_operation_path(
                    final,
                    f"interrupted build {build_id}",
                    quarantine=final_recovery,
                    quarantine_first=True,
                    defer_cancellation=True,
                )
            )
        else:
            errors.append(
                f"unverified build at {final} does not match this operation and was not removed"
            )

    errors.extend(
        recover_operation_path(
            staging,
            f"interrupted build staging directory for {build_id}",
            quarantine=staging_recovery,
            defer_cancellation=True,
        )
    )
    errors.extend(
        recover_operation_path(
            input_snapshot,
            f"private build input snapshot for {build_id}",
            quarantine=input_recovery,
            defer_cancellation=True,
        )
    )
    candidate_temp = candidate_record_path.parent / (
        f".{candidate_record_path.name}.write-candidate-{operation_id}.tmp"
    )
    candidate_temp_recovery = candidate_record_path.parent / (
        f".{candidate_record_path.name}.write-candidate-{operation_id}.recovery"
    )
    errors.extend(
        recover_operation_path(
            candidate_temp,
            f"candidate publication record temporary file for {build_id}",
            quarantine=candidate_temp_recovery,
            defer_cancellation=True,
        )
    )
    if draft and not durable_commit:
        errors.extend(
            recover_operation_path(
                candidate_record_path,
                f"uncommitted candidate publication record for {build_id}",
                quarantine=candidate_record_path.parent / f".{candidate_record_path.name}.cancelled-{operation_id}",
                defer_cancellation=True,
            )
        )
    metadata_temp = project_dir / f".{PROJECT_FILE}.write-build-{operation_id}.tmp"
    metadata_recovery = project_dir / f".{PROJECT_FILE}.write-build-{operation_id}.recovery"
    errors.extend(
        recover_operation_path(
            metadata_temp,
            f"build metadata atomic-write temporary file for {build_id}",
            quarantine=metadata_recovery,
            defer_cancellation=True,
        )
    )
    return durable_commit, errors


def reconcile_build_marker(project_dir: Path, marker: dict[str, Any]) -> list[str]:
    operation_id = marker.get("operation_id")
    build_id = marker.get("build_id")
    original_project = marker.get("original_project")
    if (
        not isinstance(operation_id, str)
        or not isinstance(build_id, str)
        or not isinstance(original_project, dict)
        or not re.fullmatch(r"build-\d{4}", build_id)
    ):
        raise ValueError("build recovery marker is incomplete")
    builds_root = safe_project_directory(project_dir, "builds")
    staging = builds_root / f".{build_id}.staging-{operation_id}"
    staging_recovery = builds_root / f".{staging.name}.recovery"
    final = builds_root / build_id
    final_recovery = builds_root / f".{build_id}.failed-publication-{operation_id}"
    input_snapshot = Path(tempfile.gettempdir()) / f"petkit-{build_id}-{operation_id}-inputs"
    input_recovery = input_snapshot.parent / f".{input_snapshot.name}.recovery"
    history_root = safe_project_directory(project_dir, "history")
    candidate_record_path = history_root / f"candidate-{build_id}.json"
    for key, expected in (
        ("staging", staging),
        ("final", final),
        ("input_snapshot", input_snapshot),
        ("candidate_record", candidate_record_path),
    ):
        if marker.get(key) != str(expected):
            raise ValueError(f"build recovery marker has an invalid {key} path")
    _durable_commit, errors = _reconcile_build_abnormal_exit(
        project_dir,
        original_project,
        build_id,
        staging,
        final,
        input_snapshot,
        input_recovery,
        staging_recovery,
        final_recovery,
        marker.get("expected_build_record") if isinstance(marker.get("expected_build_record"), dict) else None,
        candidate_record_path,
        (
            marker.get("expected_candidate_record")
            if isinstance(marker.get("expected_candidate_record"), dict)
            else None
        ),
        draft=marker.get("draft") is True,
        operation_id=operation_id,
    )
    return errors


def _preflight_edit_scope(
    project: dict[str, Any],
    previous: Path | None,
    previous_record: dict[str, Any] | None,
    current_source_hashes: dict[str, dict[str, str]],
    current_inputs: dict[str, Any],
    contract: Any,
) -> set[str]:
    active_edit = project.get("active_edit")
    accepted_build = project.get("accepted_build")
    if not active_edit:
        if accepted_build:
            raise ValueError("plan-edit is required before building after an accepted baseline")
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
        baseline_authority = baseline_inputs.get("authority")
        current_authority = current_inputs.get("authority")
        if isinstance(baseline_authority, dict) and isinstance(current_authority, dict):
            if (
                baseline_authority.get("canonical_identity_sha256")
                != current_authority.get("canonical_identity_sha256")
            ):
                changed.update(state.id for state in contract.states)
            else:
                authority_changes = {
                    key
                    for key in set(baseline_authority) | set(current_authority)
                    if baseline_authority.get(key) != current_authority.get(key)
                }
                if authority_changes == {"look_b_sha256"}:
                    changed.add("look-b")
                elif authority_changes:
                    changed.update(state.id for state in contract.look_states)
        elif baseline_inputs.get("authority_fingerprint") != current_inputs.get("authority_fingerprint"):
            raise ValueError("edit scope baseline predates canonical authority binding; run plan-edit again")
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


def _prepare_build_preflight(
    project_dir: Path,
    project: dict[str, Any],
) -> dict[str, Any]:
    """Resolve every read-only condition shared by status and build."""

    accepted_build = project.get("accepted_build")
    if accepted_build:
        try:
            accepted_record = read_json(project_dir / "builds" / str(accepted_build) / "build.json")
        except ValueError:
            accepted_record = {}
        accepted_inputs = accepted_record.get("build_inputs")
        accepted_authority = (
            accepted_inputs.get("authority_fingerprint")
            if isinstance(accepted_inputs, dict)
            else None
        )
        accepted_review_authority = accepted_record.get("review_authority_sha256")
        if (
            not isinstance(accepted_authority, str)
            or not re.fullmatch(r"[0-9a-f]{64}", accepted_authority)
            or not isinstance(accepted_record.get("pet_json_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", accepted_record["pet_json_sha256"])
            or not isinstance(accepted_review_authority, dict)
            or set(accepted_review_authority) != set(REVIEW_AUTHORITY_FILES)
            or any(
                not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
                for value in accepted_review_authority.values()
            )
        ):
            raise ValueError("accepted baseline predates integrity binding; run upgrade-project for a fresh baseline")
    contract = load_contract(2)
    _verify_variant_fork_snapshot(project_dir, project)
    authority = authority_snapshot(project_dir, project)
    qa_root = safe_project_directory(project_dir, "qa")
    ensure_tree_has_no_symlinks(qa_root, boundary=project_dir)
    validate_design_gate_artifacts(project_dir, contract)
    frames_root = project_dir / "source" / "frames"
    look_sources = {
        state_id: _required_look_source(project_dir, project, state_id)
        for state_id in ("look-a", "look-b")
    }
    current_source_hashes = _source_hashes(project_dir, project, contract, look_sources)
    standard_inspection = inspect_frames(frames_root, contract, standard_only=True)
    if not standard_inspection.get("ok"):
        errors = standard_inspection.get("errors")
        detail = "; ".join(str(item) for item in errors[:3]) if isinstance(errors, list) else "invalid frames"
        raise ValueError(f"standard frame inspection failed: {detail}")
    current_inputs = _build_inputs(project, authority)
    previous = current_build_dir(project_dir, project) if project.get("current_build") else None
    previous_record = _load_build_record(previous) if previous is not None else None
    _preflight_edit_scope(
        project,
        previous,
        previous_record,
        current_source_hashes,
        current_inputs,
        contract,
    )
    return {
        "contract": contract,
        "authority": authority,
        "frames_root": frames_root,
        "standard_inspection": standard_inspection,
        "look_sources": look_sources,
        "source_sha256": current_source_hashes,
        "build_inputs": current_inputs,
        "previous": previous,
        "previous_record": previous_record,
        "project_sha256": json_sha256(project),
    }


def _verify_build_input_snapshot(
    project_dir: Path,
    expected_project_sha256: str,
    expected_authority: dict[str, str],
    expected_sources: dict[str, dict[str, str]],
    expected_inputs: dict[str, Any],
) -> None:
    _path, current_project = load_project(project_dir)
    if json_sha256(current_project) != expected_project_sha256:
        raise ValueError("project metadata changed during build; refusing to publish a stale build")
    contract = load_contract(2)
    current_authority = authority_snapshot(project_dir, current_project)
    if current_authority != expected_authority:
        raise ValueError("canonical authority changed during build; refusing to publish a stale build")
    current_look_sources = {
        state_id: _required_look_source(project_dir, current_project, state_id)
        for state_id in ("look-a", "look-b")
    }
    current_sources = _source_hashes(project_dir, current_project, contract, current_look_sources)
    if current_sources != expected_sources:
        raise ValueError("source inputs changed during build; refusing to publish a stale build")
    if _build_inputs(current_project, current_authority) != expected_inputs:
        raise ValueError("build parameters changed during build; refusing to publish a stale build")


def _copy_build_source_snapshot(
    project_dir: Path,
    project: dict[str, Any],
    contract: Any,
    look_sources: dict[str, Path],
    expected_hashes: dict[str, dict[str, str]],
    snapshot_root: Path,
) -> tuple[Path, dict[str, Path]]:
    source_snapshot = snapshot_root / "source"
    source_snapshot.mkdir(parents=True, exist_ok=False)
    frames_snapshot = source_snapshot / "frames"
    frames_snapshot.mkdir(parents=True, exist_ok=False)
    live_frames = project_dir / "source" / "frames"
    for state in contract.standard_states:
        destination = frames_snapshot / state.id
        destination.mkdir()
        expected = expected_hashes.get(state.id)
        if not isinstance(expected, dict):
            raise ValueError(f"source snapshot is missing recorded hashes for {state.id}")
        files = source_image_files(live_frames / state.id, boundary=project_dir)
        if {path.name for path in files} != set(expected):
            raise ValueError(f"source inputs changed while snapshotting {state.id}")
        for source in files:
            target = destination / source.name
            shutil.copy2(source, target)
            if sha256_file(target) != expected[source.name]:
                raise ValueError(f"source inputs changed while snapshotting {state.id}/{source.name}")
    look_snapshot: dict[str, Path] = {}
    rows_root = source_snapshot / "rows"
    rows_root.mkdir()
    for state_id, source in look_sources.items():
        expected = expected_hashes.get(state_id)
        if not isinstance(expected, dict) or set(expected) != {source.name}:
            raise ValueError(f"look source snapshot is missing recorded hashes for {state_id}")
        state_rows = rows_root / state_id
        state_rows.mkdir()
        target = state_rows / source.name
        if source.is_symlink() or not source.resolve().is_relative_to(project_dir.resolve()):
            raise ValueError(f"look source must not be symbolic or outside the project: {source}")
        shutil.copy2(source, target)
        if sha256_file(target) != expected[source.name]:
            raise ValueError(f"look source changed while snapshotting {state_id}")
        look_snapshot[state_id] = target
    copied_hashes = _source_hashes(snapshot_root, project, contract, look_snapshot)
    if copied_hashes != expected_hashes:
        raise ValueError("private build source snapshot does not match recorded source hashes")
    return frames_snapshot, look_snapshot


def _copy_despill_cache_snapshot(
    previous: Path,
    previous_record: dict[str, Any],
    destination: Path,
) -> bool:
    recorded = previous_record.get("despill_cache_sha256")
    if not isinstance(recorded, dict) or set(recorded) != set(DESPILL_CACHE_FILES):
        return False
    destination.mkdir(parents=True, exist_ok=False)
    try:
        for name in DESPILL_CACHE_FILES:
            expected = recorded.get(name)
            source = previous / name
            target = destination / name
            if (
                not isinstance(expected, str)
                or not re.fullmatch(r"[0-9a-f]{64}", expected)
                or source.is_symlink()
                or not source.is_file()
            ):
                raise ValueError("unusable despill cache")
            shutil.copy2(source, target)
            if sha256_file(target) != expected:
                raise ValueError("changed despill cache")
        return True
    except (OSError, ValueError):
        shutil.rmtree(destination, ignore_errors=True)
        return False


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


@project_mutation
def build_project(project_value: str | Path, *, draft: bool = False) -> dict[str, Any]:
    project_dir, project = load_project(project_value)
    original_project = json.loads(json.dumps(project))
    preflight = _prepare_build_preflight(project_dir, project)
    contract = preflight["contract"]
    authority = preflight["authority"]
    current_source_hashes = preflight["source_sha256"]
    current_inputs = preflight["build_inputs"]
    previous = preflight["previous"]
    previous_record = preflight["previous_record"]
    project_snapshot_sha256 = preflight["project_sha256"]
    source_changed = (
        _changed_source_states(current_source_hashes, previous_record.get("source_sha256") if previous_record else None, contract)
        if previous is not None
        else set()
    )
    reusable_parent_eligible = (
        previous is not None
        and previous_record is not None
        and previous_record.get("build_algorithm_version") == BUILD_ALGORITHM_VERSION
        and previous_record.get("despill_processing_version") == 2
        and isinstance(previous_record.get("build_inputs"), dict)
        and previous_record["build_inputs"].get("chroma_key") == current_inputs.get("chroma_key")
        and previous_record["build_inputs"].get("chroma_threshold") == current_inputs.get("chroma_threshold")
    )
    builds_root = safe_project_directory(project_dir, "builds", create=True)
    build_id = next_build_id(project_dir)
    operation_id = uuid.uuid4().hex
    staging = builds_root / f".{build_id}.staging-{operation_id}"
    staging_recovery = builds_root / f".{staging.name}.recovery"
    final = builds_root / build_id
    final_recovery = builds_root / f".{build_id}.failed-publication-{operation_id}"
    input_snapshot = Path(tempfile.gettempdir()) / f"petkit-{build_id}-{operation_id}-inputs"
    input_recovery = input_snapshot.parent / f".{input_snapshot.name}.recovery"
    history_root = safe_project_directory(project_dir, "history")
    candidate_record_path = history_root / f"candidate-{build_id}.json"
    build_record: dict[str, Any] | None = None
    candidate_record: dict[str, Any] | None = None
    marker = {
        "schema_version": 1,
        "kind": "build",
        "operation_id": operation_id,
        "build_id": build_id,
        "draft": draft,
        "original_project": original_project,
        "staging": str(staging),
        "final": str(final),
        "input_snapshot": str(input_snapshot),
        "candidate_record": str(candidate_record_path),
        "expected_build_record": None,
        "expected_candidate_record": None,
    }
    marker_path = operation_marker_path(project_dir, operation_id)
    try:
        marker_path = write_operation_marker(project_dir, marker)
        transaction_trace("build.before-input-mkdir", path=str(input_snapshot))
        input_snapshot.mkdir(mode=0o700)
        transaction_trace("build.after-input-mkdir", path=str(input_snapshot))
        transaction_trace("build.before-staging-mkdir", path=str(staging))
        staging.mkdir(parents=True, exist_ok=False)
        transaction_trace("build.after-staging-mkdir", path=str(staging))
        snapshot_frames_root, snapshot_look_sources = _copy_build_source_snapshot(
            project_dir,
            project,
            contract,
            preflight["look_sources"],
            current_source_hashes,
            input_snapshot,
        )
        parent_atlas_snapshot: Path | None = None
        parent_contact_snapshot: Path | None = None
        parent_is_standard_only = False
        parent_snapshot_root = input_snapshot / "parent-build"
        if previous is not None and previous_record is not None:
            parent_atlas_snapshot = _copy_parent_atlas_snapshot(
                previous,
                previous_record,
                parent_snapshot_root / "spritesheet.webp",
            )
            with Image.open(parent_atlas_snapshot) as opened:
                parent_is_standard_only = opened.size == (
                    contract.width,
                    contract.standard_rows * contract.cell_height,
                )
            parent_contact_snapshot = parent_snapshot_root / "contact-sheet.png"
            make_contact_sheet(
                parent_atlas_snapshot,
                parent_contact_snapshot,
                contract,
                standard_only=parent_is_standard_only,
            )
        cache_snapshot = input_snapshot / "despill-cache"
        reusable_parent = bool(
            reusable_parent_eligible
            and previous is not None
            and previous_record is not None
            and _copy_despill_cache_snapshot(previous, previous_record, cache_snapshot)
        )
        standard_inspection = inspect_frames(snapshot_frames_root, contract, standard_only=True)
        atomic_write_json(staging / "standard-frame-inspection.json", standard_inspection)
        if not standard_inspection["ok"]:
            raise ValueError(f"standard frame inspection failed with {len(standard_inspection['errors'])} error(s)")

        standard_atlas = staging / "standard-atlas.png"
        compose_atlas(snapshot_frames_root, standard_atlas, contract, standard_only=True)
        assembled = assemble_v2(
            base_atlas=standard_atlas,
            look_row_9=snapshot_look_sources["look-a"],
            look_row_10=snapshot_look_sources["look-b"],
            output_dir=staging,
            chroma_key=project["generation"]["chroma_key"],
            chroma_threshold=float(project["generation"]["chroma_threshold"]),
            previous_raw=(cache_snapshot / "spritesheet-before-despill.png") if reusable_parent else None,
            previous_output=(cache_snapshot / "spritesheet.png") if reusable_parent else None,
            previous_report=(cache_snapshot / "despill.json") if reusable_parent else None,
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
            snapshot_previews = parent_snapshot_root / "previews"
            if _copy_complete_directory(
                previous / "previews",
                snapshot_previews,
                {f"{state_id}.gif" for state_id in all_preview_ids - render_dirty},
                parent_preview_hashes if isinstance(parent_preview_hashes, dict) else None,
            ) and _copy_complete_directory(
                snapshot_previews,
                staging / "previews",
                {f"{state_id}.gif" for state_id in all_preview_ids - render_dirty},
                parent_preview_hashes if isinstance(parent_preview_hashes, dict) else None,
            ):
                reused_preview_ids = all_preview_ids - render_dirty
                render_ids = render_dirty
            snapshot_filmstrips = parent_snapshot_root / "standard-filmstrips"
            if _copy_complete_directory(
                previous / "qa" / "standard-filmstrips",
                snapshot_filmstrips,
                {f"{state_id}.png" for state_id in all_standard_ids - filmstrip_dirty},
                parent_filmstrip_hashes if isinstance(parent_filmstrip_hashes, dict) else None,
            ) and _copy_complete_directory(
                snapshot_filmstrips,
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
                    authority["canonical_identity_sha256"],
                )
                futures["semantic"] = workers.submit(
                    make_semantic_recognition_artifacts,
                    registered_frames,
                    staging / "qa" / "semantic-recognition",
                    staging / "qa-private",
                    contract,
                    spritesheet_hash,
                    authority["canonical_identity_sha256"],
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
        pet_json_hash = sha256_file(staging / "pet.json")

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
            if parent_atlas_snapshot is None or parent_contact_snapshot is None:
                raise ValueError("parent build snapshot is unavailable for release comparison")
            change_report = compare_atlases(parent_atlas_snapshot, spritesheet, contract)
            change_report["first_build"] = False
            change_report["before"] = f"../{previous.name}/spritesheet.webp"
            change_report["after"] = "spritesheet.webp"
            change_report["source_changed_states"] = sorted(source_changed)
            make_before_after_sheet(parent_contact_snapshot, staging / "contact-sheet.png", staging / "before-after.png")
            if parent_is_standard_only:
                changed_standard = sorted(set(change_report["changed_states"]) & {state.id for state in contract.standard_states})
                alpha_preserved = _standard_alpha_identical(
                    parent_atlas_snapshot,
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
        despill_cache_hashes = _relative_file_hashes(staging, DESPILL_CACHE_FILES)
        review_authority_hashes = (
            _relative_file_hashes(staging, REVIEW_AUTHORITY_FILES)
            if not draft
            else {}
        )

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
            "canonical_identity_sha256": authority["canonical_identity_sha256"],
            "spritesheet_sha256": spritesheet_hash,
            "pet_json_sha256": pet_json_hash,
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
            "edit_scope": {
                "required": bool(project.get("accepted_build")),
                "edit_id": active_edit.get("edit_id") if active_edit else None,
                "initial_baseline_build": active_edit.get("initial_baseline_build") if active_edit else None,
                "allowed_states": sorted(active_edit.get("allowed_states", [])) if active_edit else [],
            },
            "artifact_reuse": {
                "parent_build": previous.name if reusable_parent and previous is not None else None,
                "preview_states": sorted(reused_preview_ids),
                "standard_filmstrips": sorted(reused_filmstrip_ids),
                "candidate": draft,
            },
            "artifact_sha256": artifact_hashes,
            "despill_cache_sha256": despill_cache_hashes,
            "review_authority_sha256": review_authority_hashes,
        }
        atomic_write_json(staging / "build.json", build_record)
        candidate_record = (
            {
                "schema_version": 1,
                "event": "candidate-build-created-v2",
                "build_id": build_id,
                "spritesheet_sha256": build_record["spritesheet_sha256"],
                "baseline_build": build_record["previous_build"],
                "build_record_sha256": json_sha256(build_record),
                "published_at": build_record["created_at"],
            }
            if draft
            else None
        )
        marker["expected_build_record"] = build_record
        marker["expected_candidate_record"] = candidate_record
        write_operation_marker(project_dir, marker)
        snapshot_cleanup_errors = _cleanup_private_snapshot(
            input_snapshot,
            "private build input snapshot",
            input_recovery,
        )
        if snapshot_cleanup_errors:
            raise RuntimeError("; ".join(snapshot_cleanup_errors))
        _verify_build_input_snapshot(
            project_dir,
            project_snapshot_sha256,
            authority,
            current_source_hashes,
            current_inputs,
        )
        transaction_trace("build.before-publish", staging=str(staging), final=str(final))
        os.replace(staging, final)
        transaction_trace("build.after-publish", staging=str(staging), final=str(final))
        post_commit_warnings: list[str] = []
        if draft:
            atomic_write_json(
                candidate_record_path,
                candidate_record,
                operation_id=f"candidate-{operation_id}",
            )
            transaction_trace("build.after-candidate-record", path=str(candidate_record_path))
            try:
                append_event(
                    project_dir,
                    "candidate-build-created-v2",
                    {
                        "build_id": build_id,
                        "spritesheet_sha256": build_record["spritesheet_sha256"],
                        "baseline_build": build_record["previous_build"],
                    },
                )
                transaction_trace("build.after-candidate-event", build_id=build_id)
            except (OSError, ValueError) as exc:
                post_commit_warnings.append(f"candidate build was published but its event could not be recorded: {exc}")
        else:
            project["current_build"] = build_id
            project["status"] = "review"
            if active_edit:
                project["active_edit"]["comparison_build"] = build_id
                project["active_edit"]["latest_build"] = build_id
            save_project(
                project_dir,
                project,
                expected_current_sha256=project_snapshot_sha256,
                operation_id=f"build-{operation_id}",
                reconcile_pending=False,
            )
            try:
                append_event(
                    project_dir,
                    "build-created-v2",
                    {"build_id": build_id, "spritesheet_sha256": build_record["spritesheet_sha256"], "previous_build": build_record["previous_build"]},
                )
            except (OSError, ValueError) as exc:
                post_commit_warnings.append(f"build and project pointer were committed but the event could not be recorded: {exc}")
        result = {
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
            "post_commit_warnings": post_commit_warnings,
        }
        marker_cleanup_errors = remove_operation_marker(marker_path)
        if marker_cleanup_errors:
            raise TransactionRecoveryError("; ".join(marker_cleanup_errors))
        return result
    except BaseException as operation_error:
        durable_commit, recovery_errors = _reconcile_build_abnormal_exit(
            project_dir,
            original_project,
            build_id,
            staging,
            final,
            input_snapshot,
            input_recovery,
            staging_recovery,
            final_recovery,
            build_record,
            candidate_record_path,
            candidate_record,
            draft=draft,
            operation_id=operation_id,
        )
        if not recovery_errors:
            recovery_errors.extend(remove_operation_marker(marker_path, defer_cancellation=True))
        if recovery_errors:
            raise TransactionRecoveryError(
                f"build failed ({type(operation_error).__name__}: {operation_error}); "
                f"transaction recovery was incomplete; cleanup was incomplete: {'; '.join(recovery_errors)}"
            ) from operation_error
        if durable_commit:
            operation_error.add_note(
                f"build {build_id} was durably committed at {final} before cancellation; it was preserved"
            )
        raise


def _build_dir(project_dir: Path, build_id: Any) -> Path:
    if not isinstance(build_id, str) or not re.fullmatch(r"build-\d{4}", build_id):
        raise ValueError(f"invalid build id: {build_id!r}")
    builds_root = safe_project_directory(project_dir, "builds")
    path = builds_root / build_id
    if path.is_symlink() or (path.exists() and not path.resolve().is_relative_to(builds_root.resolve())):
        raise ValueError(f"build directory must not be symbolic or outside the project: {path}")
    return path


def _verify_installable_package(
    package_dir: Path,
    project_id: str,
    build_record: dict[str, Any],
) -> None:
    spritesheet = package_dir / "spritesheet.webp"
    manifest_path = package_dir / "pet.json"
    expected_atlas_hash = build_record.get("spritesheet_sha256")
    if not isinstance(expected_atlas_hash, str) or sha256_file(spritesheet) != expected_atlas_hash:
        raise ValueError("build spritesheet no longer matches its immutable build record")
    expected_manifest_hash = build_record.get("pet_json_sha256")
    if (
        not isinstance(expected_manifest_hash, str)
        or not re.fullmatch(r"[0-9a-f]{64}", expected_manifest_hash)
        or sha256_file(manifest_path) != expected_manifest_hash
    ):
        raise ValueError("build manifest no longer matches its immutable build record")
    manifest = read_json(manifest_path)
    if manifest.get("id") != project_id:
        raise ValueError("build manifest id does not match the selected project")
    if manifest.get("spriteVersionNumber") != 2:
        raise ValueError("build manifest is not a V2 pet manifest")
    if manifest.get("spritesheetPath") != "spritesheet.webp":
        raise ValueError("build manifest spritesheetPath must be exactly 'spritesheet.webp'")


def _verify_build_artifact(project_dir: Path, project: dict[str, Any], build_id: str) -> tuple[Path, dict[str, Any]]:
    build_dir = _build_dir(project_dir, build_id)
    build_record_path = build_dir / "build.json"
    spritesheet = build_dir / "spritesheet.webp"
    manifest_path = build_dir / "pet.json"
    if (
        not build_dir.is_dir()
        or not build_record_path.is_file()
        or not spritesheet.is_file()
        or not manifest_path.is_file()
    ):
        raise ValueError(f"build is incomplete: {build_dir}")
    build_record = read_json(build_record_path)
    if build_record.get("build_id") != build_id or build_record.get("pet_id") != project["id"]:
        raise ValueError("build record identity does not match the selected project and build")
    if build_record.get("contract_version") != 2:
        raise ValueError("only V2 builds are supported")
    if build_record.get("build_kind", RELEASE_BUILD_KIND) != RELEASE_BUILD_KIND:
        raise ValueError("candidate builds cannot be reviewed, accepted, or installed")
    _verify_installable_package(build_dir, project["id"], build_record)
    _verify_hash_manifest(
        build_dir,
        build_record.get("review_authority_sha256"),
        REVIEW_AUTHORITY_FILES,
        "private review authority",
    )
    fresh = validate_atlas(spritesheet, load_contract(2))
    if not fresh.get("ok"):
        raise ValueError("build spritesheet no longer passes deterministic V2 validation")
    build_inputs = build_record.get("build_inputs")
    authority = build_inputs.get("authority") if isinstance(build_inputs, dict) else None
    canonical_hash = build_record.get("canonical_identity_sha256")
    if (
        not isinstance(canonical_hash, str)
        or not isinstance(authority, dict)
        or authority.get("canonical_identity_sha256") != canonical_hash
    ):
        raise ValueError("build record is missing its canonical identity binding")
    return build_dir, build_record


def _verify_live_build_authority(
    project_dir: Path,
    project: dict[str, Any],
    build_record: dict[str, Any],
    *,
    identity_only: bool = False,
) -> dict[str, str]:
    live_identity_hash = _canonical_identity_hash(project_dir, project)
    if build_record.get("canonical_identity_sha256") != live_identity_hash:
        raise ValueError("canonical identity no longer matches the selected build")
    if identity_only:
        build_inputs = build_record.get("build_inputs")
        authority = build_inputs.get("authority") if isinstance(build_inputs, dict) else None
        return authority if isinstance(authority, dict) else {}
    live_authority = authority_snapshot(project_dir, project)
    build_inputs = build_record.get("build_inputs")
    recorded_authority = build_inputs.get("authority") if isinstance(build_inputs, dict) else None
    if recorded_authority != live_authority:
        raise ValueError("canonical authority inputs no longer match the selected build")
    contract = load_contract(2)
    look_sources = {
        state_id: _required_look_source(project_dir, project, state_id)
        for state_id in ("look-a", "look-b")
    }
    live_source_hashes = _source_hashes(project_dir, project, contract, look_sources)
    if build_record.get("source_sha256") != live_source_hashes:
        raise ValueError("source inputs no longer match the selected build")
    if build_inputs != _build_inputs(project, live_authority):
        raise ValueError("build parameters no longer match the selected build")
    return live_authority


def _prepare_review_preflight(
    project_dir: Path,
    project: dict[str, Any],
    build_id: str,
) -> tuple[Path, dict[str, Any]]:
    if project.get("current_build") != build_id:
        raise ValueError("only the project's current release build can be reviewed")
    build_dir, record = _verify_build_artifact(project_dir, project, build_id)
    _verify_live_build_authority(project_dir, project, record)
    return build_dir, record


def _prepare_review_publication_preflight(
    project_dir: Path,
    project: dict[str, Any],
    build_id: str,
) -> tuple[Path, dict[str, Any]]:
    build_dir, record = _prepare_review_preflight(project_dir, project, build_id)
    final = safe_project_directory(project_dir, "reviews") / build_id
    if os.path.lexists(final):
        raise ValueError(f"a published review package already exists: {final}")
    return build_dir, record


def _validate_fresh_review_evidence(
    review_dir: Path,
    build_dir: Path,
    atlas_hash: str,
    canonical_identity_sha256: str,
) -> None:
    semantic_names = [f"semantic-recognition-{index:02d}.json" for index in range(1, 4)]
    visual_names = [f"independent-visual-qa-{index:02d}.json" for index in range(1, 4)]
    observed_semantic = sorted(path.name for path in review_dir.glob("semantic-recognition-*.json"))
    observed_visual = sorted(path.name for path in review_dir.glob("independent-visual-qa-*.json"))
    if observed_semantic != semantic_names:
        raise ValueError("review package requires exactly three fresh semantic recognition verdicts")
    if observed_visual != visual_names:
        raise ValueError("review package requires exactly three fresh independent visual QA verdicts")
    answer_key_path = build_dir / "qa-private" / "semantic-recognition-answer-key.json"
    if not answer_key_path.is_file():
        raise ValueError("V2 build is missing the private semantic-recognition answer key")
    answer_key = read_json(answer_key_path)
    semantic_reviewer_ids: list[str] = []
    for name in semantic_names:
        payload = read_json(review_dir / name)
        validate_semantic_recognition(
            payload,
            answer_key,
            atlas_hash,
            canonical_identity_sha256,
        )
        semantic_reviewer_ids.append(payload["reviewer_id"].strip())
    if len(set(semantic_reviewer_ids)) != 3:
        raise ValueError("V2 semantic review requires three distinct reviewer identifiers")
    visual_reviewer_ids: list[str] = []
    for name in visual_names:
        payload = read_json(review_dir / name)
        validate_visual_qa(payload, atlas_hash, canonical_identity_sha256)
        visual_reviewer_ids.append(payload["reviewer_id"].strip())
    if len(set(visual_reviewer_ids)) != 3:
        raise ValueError("V2 visual review requires three distinct reviewer identifiers")


def _verify_fresh_direction_evidence(
    review_dir: Path,
    build_dir: Path,
    atlas_hash: str,
    canonical_identity_sha256: str,
) -> None:
    semantics_path = review_dir / "direction-semantics.json"
    if not semantics_path.is_file():
        raise ValueError("review package is missing fresh direction semantics")
    semantics = read_json(semantics_path)
    validate_direction_semantics(
        semantics,
        load_contract(2),
        atlas_hash,
        canonical_identity_sha256,
    )
    verdicts = sorted(
        path
        for path in review_dir.iterdir()
        if path.is_file() and re.fullmatch(r"blind-verdict-\d{2}\.json", path.name)
    )
    if len(verdicts) < 3 or len(verdicts) % 2 == 0:
        raise ValueError("review package requires an odd number of at least three fresh blind verdicts")
    expected_names = [f"blind-verdict-{index:02d}.json" for index in range(1, len(verdicts) + 1)]
    if [path.name for path in verdicts] != expected_names:
        raise ValueError("blind direction verdict numbering must be contiguous")
    combined_path = review_dir / "blind-verdict-combined.json"
    validation_path = review_dir / "blind-verdict-validation.json"
    if not combined_path.is_file() or not validation_path.is_file():
        raise ValueError("review package is missing combined blind direction evidence")
    with tempfile.TemporaryDirectory(prefix="petkit-review-replay-") as temporary:
        replay = combine_and_validate_blind_reviews(
            answer_key=build_dir / "qa-private" / "direction-blind-answer-key.json",
            verdicts=verdicts,
            output_dir=Path(temporary),
            canonical_identity_sha256=canonical_identity_sha256,
            atlas_sha256=atlas_hash,
        )
        if semantics["reviewer_id"].strip() in replay["reviewer_ids"]:
            raise ValueError("direction-semantics reviewer must be distinct from blind direction reviewers")
        if read_json(Path(replay["combined"])) != read_json(combined_path):
            raise ValueError("stored blind direction majority does not match the fresh verdicts")
        if replay["validation"] != read_json(validation_path):
            raise ValueError("stored blind direction validation does not match replayed evidence")


def _direction_evidence_files(review_dir: Path, summary: dict[str, Any]) -> tuple[Path, list[Path]]:
    if summary.get("direction_review_inherited") is True:
        base = review_dir / "inherited-direction"
        files = sorted(path for path in base.rglob("*") if path.is_file()) if base.is_dir() else []
    else:
        base = review_dir
        files = sorted(
            path
            for path in review_dir.iterdir()
            if path.is_file()
            and (
                path.name == "direction-semantics.json"
                or path.name in {"blind-verdict-combined.json", "blind-verdict-validation.json"}
                or re.fullmatch(r"blind-verdict-\d{2}\.json", path.name)
            )
        )
    if not files:
        raise ValueError("parent review has no direction evidence to inherit")
    return base, files


def _inherit_direction_review(
    project_dir: Path,
    project: dict[str, Any],
    build_id: str,
    parent_build_id: str,
    output_dir: Path,
) -> dict[str, Any]:
    if parent_build_id == build_id:
        raise ValueError("a build cannot inherit its own direction review")
    build_dir, record = _prepare_review_preflight(project_dir, project, build_id)
    parent_dir, parent_record = _verify_build_artifact(project_dir, project, parent_build_id)
    _verify_live_build_authority(project_dir, project, parent_record, identity_only=True)
    parent_review_dir = safe_project_directory(project_dir, "reviews") / parent_build_id
    if parent_review_dir.is_symlink():
        raise ValueError("parent review directory must not be symbolic")
    parent_summary = read_json(parent_review_dir / "review-summary.json")
    if parent_record.get("build_inputs") != record.get("build_inputs"):
        raise ValueError("cannot inherit direction review after canonical build inputs changed")
    current_cells = _direction_cell_hashes(build_dir / "spritesheet.webp", load_contract(2))
    parent_cells = _direction_cell_hashes(parent_dir / "spritesheet.webp", load_contract(2))
    if current_cells != parent_cells:
        raise ValueError("cannot inherit direction review because neutral or look-direction pixels changed")
    source_base, source_files = _direction_evidence_files(parent_review_dir, parent_summary)
    inherited_dir = output_dir / "inherited-direction"
    for source in source_files:
        relative = source.relative_to(source_base)
        target = inherited_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return {
        "direction_semantics_ok": True,
        "blind_majority_ok": True,
        "direction_review_parent_build": parent_build_id,
        "direction_review_parent_atlas_sha256": parent_record["spritesheet_sha256"],
        "direction_review_cell_hashes": current_cells,
        "continuity_override_note": parent_summary.get("continuity_override_note"),
    }


def _verify_inherited_direction_evidence(
    project_dir: Path,
    project: dict[str, Any],
    build_id: str,
    build_dir: Path,
    build_record: dict[str, Any],
    review_dir: Path,
    summary: dict[str, Any],
    seen: set[str],
) -> None:
    parent_build_id = summary.get("direction_review_parent_build")
    if not isinstance(parent_build_id, str) or parent_build_id == build_id:
        raise ValueError("inherited direction review has an invalid parent build")
    parent_dir, parent_record = _verify_build_artifact(project_dir, project, parent_build_id)
    if summary.get("direction_review_parent_atlas_sha256") != parent_record.get("spritesheet_sha256"):
        raise ValueError("inherited direction review parent atlas binding is stale")
    if parent_record.get("build_inputs") != build_record.get("build_inputs"):
        raise ValueError("inherited direction review build inputs no longer match")
    current_cells = _direction_cell_hashes(build_dir / "spritesheet.webp", load_contract(2))
    parent_cells = _direction_cell_hashes(parent_dir / "spritesheet.webp", load_contract(2))
    if current_cells != parent_cells or summary.get("direction_review_cell_hashes") != current_cells:
        raise ValueError("inherited direction review cell lineage no longer matches")
    parent_review_dir, parent_summary = _verify_review_package(
        project_dir,
        project,
        parent_build_id,
        parent_dir,
        parent_record,
        seen=seen,
        live_binding="identity",
    )
    source_base, source_files = _direction_evidence_files(parent_review_dir, parent_summary)
    expected_names = [path.relative_to(source_base).as_posix() for path in source_files]
    inherited_dir = review_dir / "inherited-direction"
    actual_files = sorted(path for path in inherited_dir.rglob("*") if path.is_file()) if inherited_dir.is_dir() else []
    actual_names = [path.relative_to(inherited_dir).as_posix() for path in actual_files]
    if expected_names != actual_names:
        raise ValueError("inherited direction evidence is missing or incomplete")
    if any(
        source.read_bytes() != (inherited_dir / relative).read_bytes()
        for source, relative in zip(source_files, expected_names)
    ):
        raise ValueError("inherited direction evidence no longer matches its published review")


def _verify_review_package(
    project_dir: Path,
    project: dict[str, Any],
    build_id: str,
    build_dir: Path,
    build_record: dict[str, Any],
    review_dir: Path | None = None,
    *,
    seen: set[str] | None = None,
    live_binding: str = "full",
) -> tuple[Path, dict[str, Any]]:
    visited = set() if seen is None else set(seen)
    if build_id in visited:
        raise ValueError("inherited direction review lineage contains a cycle")
    visited.add(build_id)
    if live_binding not in {"full", "identity"}:
        raise ValueError(f"unsupported review authority binding: {live_binding}")
    _verify_live_build_authority(
        project_dir,
        project,
        build_record,
        identity_only=live_binding == "identity",
    )
    selected_review_dir = review_dir or (safe_project_directory(project_dir, "reviews") / build_id)
    if selected_review_dir.is_symlink():
        raise ValueError("review directory must not be symbolic")
    summary_path = selected_review_dir / "review-summary.json"
    if not summary_path.is_file():
        raise ValueError("V2 acceptance requires a complete published review package")
    summary = read_json(summary_path)
    canonical_identity_sha256 = build_record["canonical_identity_sha256"]
    if (
        summary.get("schema_version") != 3
        or summary.get("complete") is not True
        or summary.get("ok") is not True
        or summary.get("build_id") != build_id
        or summary.get("atlas_sha256") != build_record.get("spritesheet_sha256")
        or summary.get("canonical_identity_sha256") != canonical_identity_sha256
    ):
        raise ValueError("review summary is incomplete or does not match the selected build")
    _validate_fresh_review_evidence(
        selected_review_dir,
        build_dir,
        build_record["spritesheet_sha256"],
        canonical_identity_sha256,
    )
    if (
        summary.get("semantic_recognition_ok") is not True
        or summary.get("semantic_recognition_count") != 3
        or summary.get("semantic_recognition_unanimous") is not True
        or summary.get("independent_visual_qa_ok") is not True
        or summary.get("independent_visual_qa_count") != 3
        or summary.get("independent_visual_qa_unanimous") is not True
    ):
        raise ValueError("review summary does not record the required fresh semantic and visual verdicts")
    continuity = read_json(build_dir / "qa" / "direction-continuity.json")
    continuity_required = bool(continuity.get("reviewRequired"))
    if summary.get("continuity_review_required") is not continuity_required:
        raise ValueError("review summary continuity requirement does not match the immutable build report")
    if summary.get("direction_review_inherited") is True:
        _verify_inherited_direction_evidence(
            project_dir,
            project,
            build_id,
            build_dir,
            build_record,
            selected_review_dir,
            summary,
            visited,
        )
    else:
        _verify_fresh_direction_evidence(
            selected_review_dir,
            build_dir,
            build_record["spritesheet_sha256"],
            canonical_identity_sha256,
        )
    if summary.get("direction_semantics_ok") is not True or summary.get("blind_majority_ok") is not True:
        raise ValueError("review summary does not record passing direction evidence")
    if continuity_required and not str(summary.get("continuity_override_note") or "").strip():
        raise ValueError("review package is missing its required continuity override note")
    return selected_review_dir, summary


@project_mutation
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
    build_dir, record = _prepare_review_publication_preflight(project_dir, project, build_id)
    atlas_hash = record["spritesheet_sha256"]
    canonical_identity_sha256 = record["canonical_identity_sha256"]
    if bool(inherit_direction_from) == bool(direction_semantics is not None or blind_verdicts):
        raise ValueError("provide either inherited direction review or fresh direction evidence, but not both")
    if len(semantic_verdicts) != 3:
        raise ValueError("V2 review requires exactly three independent semantic recognition verdicts")
    if len(independent_visual_qas) != 3:
        raise ValueError("V2 review requires exactly three independent visual QA verdicts")
    reviews_root = safe_project_directory(project_dir, "reviews", create=True)
    final = reviews_root / build_id
    staging = reviews_root / f".{build_id}.staging-{uuid.uuid4().hex}"
    staging.mkdir(exist_ok=False)
    try:
        inherited_direction: dict[str, Any] | None = None
        if inherit_direction_from:
            inherited_direction = _inherit_direction_review(
                project_dir,
                project,
                build_id,
                inherit_direction_from,
                staging,
            )
            if not continuity_override_note.strip():
                inherited_note = inherited_direction.get("continuity_override_note")
                if isinstance(inherited_note, str):
                    continuity_override_note = inherited_note
        else:
            if direction_semantics is None or len(blind_verdicts) < 3 or len(blind_verdicts) % 2 == 0:
                raise ValueError("fresh direction review requires semantics and an odd number of at least three blind verdicts")
            shutil.copy2(direction_semantics, staging / "direction-semantics.json")
            direction_semantics_payload = read_json(staging / "direction-semantics.json")
            validate_direction_semantics(
                direction_semantics_payload,
                load_contract(2),
                atlas_hash,
                canonical_identity_sha256,
            )
            blind = combine_and_validate_blind_reviews(
                answer_key=build_dir / "qa-private" / "direction-blind-answer-key.json",
                verdicts=blind_verdicts,
                output_dir=staging,
                canonical_identity_sha256=canonical_identity_sha256,
                atlas_sha256=atlas_hash,
            )
            if direction_semantics_payload["reviewer_id"].strip() in blind["reviewer_ids"]:
                raise ValueError("direction-semantics reviewer must be distinct from blind direction reviewers")
            if not blind["validation"].get("ok"):
                raise ValueError("fresh direction review did not pass")
        for index, source in enumerate(independent_visual_qas, start=1):
            shutil.copy2(source, staging / f"independent-visual-qa-{index:02d}.json")
        for index, source in enumerate(semantic_verdicts, start=1):
            shutil.copy2(source, staging / f"semantic-recognition-{index:02d}.json")
        _validate_fresh_review_evidence(
            staging,
            build_dir,
            atlas_hash,
            canonical_identity_sha256,
        )
        continuity = read_json(build_dir / "qa" / "direction-continuity.json")
        if continuity.get("reviewRequired") and not continuity_override_note.strip():
            raise ValueError("direction continuity warnings require an explicit review/override note")
        summary = {
            "schema_version": 3,
            "complete": True,
            "reviewed_at": now_iso(),
            "build_id": build_id,
            "atlas_sha256": atlas_hash,
            "canonical_identity_sha256": canonical_identity_sha256,
            "direction_semantics_ok": True,
            "blind_majority_ok": True,
            "independent_visual_qa_ok": True,
            "independent_visual_qa_count": 3,
            "independent_visual_qa_unanimous": True,
            "semantic_recognition_ok": True,
            "semantic_recognition_count": 3,
            "semantic_recognition_unanimous": True,
            "continuity_review_required": bool(continuity.get("reviewRequired")),
            "continuity_override_note": continuity_override_note.strip() or None,
            "direction_review_inherited": bool(inherited_direction),
            "direction_review_parent_build": inherited_direction.get("direction_review_parent_build") if inherited_direction else None,
            "direction_review_parent_atlas_sha256": inherited_direction.get("direction_review_parent_atlas_sha256") if inherited_direction else None,
            "direction_review_cell_hashes": inherited_direction.get("direction_review_cell_hashes") if inherited_direction else None,
            "ok": True,
        }
        atomic_write_json(staging / "review-summary.json", summary)
        _verify_review_package(
            project_dir,
            project,
            build_id,
            build_dir,
            record,
            review_dir=staging,
        )
        os.replace(staging, final)
        event_warning: str | None = None
        try:
            append_event(project_dir, "direction-review-completed", {"build_id": build_id, "atlas_sha256": atlas_hash})
        except (OSError, ValueError) as exc:
            event_warning = f"review was published but its event could not be recorded: {exc}"
        return {
            "ok": True,
            "project": str(project_dir),
            "review_dir": str(final),
            "event_warning": event_warning,
            **summary,
        }
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _verify_edit_scope_for_acceptance(
    project: dict[str, Any],
    build_id: str,
    build_record: dict[str, Any],
) -> None:
    accepted_build = project.get("accepted_build")
    if not accepted_build:
        return
    if accepted_build == build_id:
        if project.get("current_build") != build_id or project.get("active_edit") is not None:
            raise ValueError("cannot re-accept the accepted baseline while a divergent edit is active")
        return
    active_edit = project.get("active_edit")
    scope = build_record.get("edit_scope")
    edit_id = build_record.get("edit_id")
    if (
        not isinstance(scope, dict)
        or scope.get("required") is not True
        or not isinstance(edit_id, str)
        or not isinstance(active_edit, dict)
        or active_edit.get("edit_id") != edit_id
        or active_edit.get("latest_build") != build_id
    ):
        raise ValueError("acceptance requires the active edit scope that covers this post-baseline build")


def _prepare_accept_preflight(
    project_dir: Path,
    project: dict[str, Any],
    build_id: str,
) -> tuple[Path, dict[str, Any], Path, dict[str, Any]]:
    build_dir, record = _verify_build_artifact(project_dir, project, build_id)
    review_dir, review_summary = _verify_review_package(
        project_dir,
        project,
        build_id,
        build_dir,
        record,
    )
    _verify_edit_scope_for_acceptance(project, build_id, record)
    for report_name in ("validation.json", "frame-inspection.json"):
        report = read_json(build_dir / report_name)
        if not report.get("ok"):
            raise ValueError(f"cannot accept a build with failing {report_name}")
    return build_dir, record, review_dir, review_summary


@project_mutation
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
    project_snapshot_sha256 = json_sha256(project)
    _build_dir_path, _record, review_dir, _review_summary = _prepare_accept_preflight(
        project_dir,
        project,
        selected,
    )
    review_path = review_dir / "review-summary.json"
    if not confirm_visual_qa:
        raise ValueError("acceptance requires explicit visual QA confirmation")
    if not review_note.strip():
        raise ValueError("acceptance requires a concise visual QA review note")
    active_edit = project.get("active_edit")
    acceptance = {
        "schema_version": 3,
        "accepted_at": now_iso(),
        "build_id": selected,
        "visual_qa_confirmed": True,
        "direction_review": str(review_path.relative_to(project_dir)),
        "review_note": review_note.strip(),
        "edit_id": active_edit.get("edit_id") if active_edit else None,
    }
    history_root = safe_project_directory(project_dir, "history", create=True)
    acceptance_path = history_root / f"acceptance-{selected}.json"
    if os.path.lexists(acceptance_path):
        raise ValueError(f"acceptance record already exists: {acceptance_path}")
    staging = history_root / f".{acceptance_path.name}.staging-{uuid.uuid4().hex}"
    published_record = False
    committed = False
    try:
        atomic_write_json(staging, acceptance)
        os.replace(staging, acceptance_path)
        published_record = True
        project["current_build"] = selected
        project["accepted_build"] = selected
        project["status"] = "accepted"
        if active_edit and active_edit.get("latest_build") == selected:
            project["active_edit"] = None
        save_project(
            project_dir,
            project,
            expected_current_sha256=project_snapshot_sha256,
        )
        committed = True
    except Exception:
        staging.unlink(missing_ok=True)
        if published_record and not committed:
            acceptance_path.unlink(missing_ok=True)
        raise
    post_commit_warnings: list[str] = []
    try:
        append_event(project_dir, "build-accepted-v2", acceptance)
    except (OSError, ValueError) as exc:
        post_commit_warnings.append(
            f"acceptance and project pointer were committed but the event could not be recorded: {exc}"
        )
    return {
        "ok": True,
        "project": str(project_dir),
        "post_commit_warnings": post_commit_warnings,
        **acceptance,
    }


def _prepare_install_preflight(
    project_dir: Path,
    project: dict[str, Any],
    build_id: str,
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    if build_id != project.get("accepted_build"):
        raise ValueError("only the project's accepted build may be installed")
    build_dir, record = _verify_build_artifact(project_dir, project, build_id)
    _verify_review_package(
        project_dir,
        project,
        build_id,
        build_dir,
        record,
        live_binding="identity",
    )
    acceptance_path = safe_project_directory(project_dir, "history") / f"acceptance-{build_id}.json"
    if acceptance_path.is_symlink():
        raise ValueError("acceptance record must not be symbolic")
    if not acceptance_path.is_file():
        raise ValueError("accepted build is missing its acceptance record")
    acceptance = read_json(acceptance_path)
    expected_review = (Path("reviews") / build_id / "review-summary.json").as_posix()
    if (
        acceptance.get("build_id") != build_id
        or acceptance.get("visual_qa_confirmed") is not True
        or not str(acceptance.get("review_note") or "").strip()
        or acceptance.get("direction_review") != expected_review
    ):
        raise ValueError("accepted build has an incomplete or mismatched acceptance record")
    return build_dir, record, acceptance


def verify_variant_parent_copy(
    copied_project_dir: Path,
    project: dict[str, Any],
    accepted_record: dict[str, Any],
) -> None:
    """Verify copied fork inputs against the accepted release, not mutable parent paths."""

    _verify_live_build_authority(copied_project_dir, project, accepted_record)


@contextmanager
def verified_variant_parent_snapshot(
    project_dir: Path,
    project: dict[str, Any],
    build_id: str,
    *,
    operation_id: str | None = None,
) -> Iterator[tuple[Path, dict[str, Any]]]:
    """Yield a private, verified copy of every parent path consumed by a new variant."""

    _build_dir_path, accepted_record, _acceptance = _prepare_install_preflight(
        project_dir,
        project,
        build_id,
    )
    _verify_live_build_authority(project_dir, project, accepted_record)
    operation_id = operation_id or uuid.uuid4().hex
    snapshot_root = Path(tempfile.gettempdir()) / f"petkit-variant-{build_id}-{operation_id}-inputs"
    snapshot_recovery = snapshot_root.parent / f".{snapshot_root.name}.recovery"
    try:
        transaction_trace("variant-parent.before-snapshot-mkdir", path=str(snapshot_root))
        snapshot_root.mkdir(mode=0o700)
        transaction_trace("variant-parent.after-snapshot-mkdir", path=str(snapshot_root))
        copy_tree_without_symlinks(
            project_dir / "references",
            snapshot_root / "references",
            boundary=project_dir,
        )
        copy_tree_without_symlinks(
            project_dir / "source",
            snapshot_root / "source",
            boundary=project_dir,
        )
        identity_file = project_dir / IDENTITY_FILE
        if identity_file.is_symlink() or not identity_file.is_file():
            raise ValueError(f"variant identity record must be a regular project file: {identity_file}")
        shutil.copy2(identity_file, snapshot_root / IDENTITY_FILE)
        verify_variant_parent_copy(snapshot_root, project, accepted_record)
        yield snapshot_root, accepted_record
    except BaseException as operation_error:
        cleanup_errors = recover_operation_path(
            snapshot_root,
            "private variant parent snapshot",
            quarantine=snapshot_recovery,
            defer_cancellation=True,
        )
        if cleanup_errors:
            raise TransactionRecoveryError(
                f"variant snapshot operation failed ({type(operation_error).__name__}: {operation_error}); "
                f"transaction recovery was incomplete; cleanup was incomplete: {'; '.join(cleanup_errors)}"
            ) from operation_error
        raise
    try:
        cleanup_errors = recover_operation_path(
            snapshot_root,
            "private variant parent snapshot",
            quarantine=snapshot_recovery,
        )
        if cleanup_errors:
            raise TransactionRecoveryError("; ".join(cleanup_errors))
    except BaseException as cleanup_error:
        recovery_errors = recover_operation_path(
            snapshot_root,
            "private variant parent snapshot",
            quarantine=snapshot_recovery,
            defer_cancellation=True,
        )
        if recovery_errors:
            raise TransactionRecoveryError(
                f"variant snapshot cleanup failed ({type(cleanup_error).__name__}: {cleanup_error}); "
                f"transaction recovery was incomplete; cleanup was incomplete: {'; '.join(recovery_errors)}"
            ) from cleanup_error
        raise


def preflight_phase(
    project_dir: Path,
    project: dict[str, Any],
    phase: str,
    build_id: str | None = None,
) -> dict[str, Any]:
    """Return a concrete, read-only blocker for one workflow boundary."""

    selected: str | None = build_id
    try:
        if phase == "build":
            _prepare_build_preflight(project_dir, project)
        elif phase == "review":
            selected = selected or project.get("current_build")
            if not selected:
                raise ValueError("project has no current release build to review")
            _prepare_review_publication_preflight(project_dir, project, selected)
        elif phase == "accept":
            selected = selected or project.get("current_build")
            if not selected:
                raise ValueError("project has no current release build to accept")
            _prepare_accept_preflight(project_dir, project, selected)
        elif phase == "install":
            selected = selected or project.get("accepted_build")
            if not selected:
                raise ValueError("project has no accepted build to install")
            _prepare_install_preflight(project_dir, project, selected)
        else:
            raise ValueError(f"unsupported preflight phase: {phase}")
    except (OSError, ValueError) as exc:
        return {
            "ok": False,
            "phase": phase,
            "build_id": selected,
            "blockers": [{"code": f"{phase}.blocked", "message": str(exc)}],
        }
    return {"ok": True, "phase": phase, "build_id": selected, "blockers": []}


def _resolve_install_target(
    project_dir: Path,
    target_root: Path,
    pet_id: str,
) -> tuple[Path, Path]:
    """Resolve a package target without creating paths and reject project overlap."""

    project_root = project_dir.resolve()
    requested_root = target_root.expanduser().absolute()
    if requested_root.is_symlink():
        raise ValueError(f"install root must not be a symbolic link: {requested_root}")
    resolved_root = requested_root.resolve(strict=False)
    lexical_target = resolved_root / pet_id
    if lexical_target.is_symlink():
        raise ValueError(f"install target must not be a symbolic link: {lexical_target}")
    resolved_target = lexical_target.resolve(strict=False)
    if resolved_target.parent != resolved_root:
        raise ValueError("install target must resolve directly inside the selected install root")
    if paths_overlap(project_root, lexical_target) or paths_overlap(project_root, resolved_target):
        raise ValueError("install target and editable project must not be equal or contain one another")
    return resolved_root, resolved_target


def _copy_package(build_dir: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    for name in PACKAGE_FILES:
        source = build_dir / name
        if source.is_symlink() or not source.is_file():
            raise ValueError(f"build is missing package file: {source}")
        shutil.copy2(source, destination / name)


def _copy_installed_package(source_dir: Path, destination: Path) -> dict[str, str]:
    if source_dir.is_symlink() or not source_dir.is_dir():
        raise ValueError(f"installed package is not a regular directory: {source_dir}")
    hashes: dict[str, str] = {}
    destination.mkdir(parents=True, exist_ok=False)
    try:
        for name in PACKAGE_FILES:
            source = source_dir / name
            if source.is_symlink() or not source.is_file():
                raise ValueError(f"installed package contains an invalid package file: {source}")
            shutil.copy2(source, destination / name)
            hashes[name] = sha256_file(destination / name)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return hashes


def _write_backup_sidecar(
    backup_dir: Path,
    pet_id: str,
    hashes: dict[str, str],
    created_at_ns: int,
    operation: str,
) -> dict[str, Any]:
    sidecar = {
        "schema_version": 1,
        "created_at": now_iso(),
        "created_at_ns": created_at_ns,
        "pet_id": pet_id,
        "files": hashes,
        "provenance": {"tool": "petkit", "operation": operation},
    }
    atomic_write_json(backup_dir / BACKUP_SIDECAR, sidecar)
    return sidecar


def _validate_package_manifest(package: Path, pet_id: str, *, require_v2: bool = False) -> dict[str, Any]:
    manifest = read_json(package / "pet.json")
    if manifest.get("id") != pet_id:
        raise ValueError("backup package manifest does not match this project")
    if manifest.get("spritesheetPath") != "spritesheet.webp":
        raise ValueError("backup package manifest must reference the copied spritesheet.webp")
    version = manifest.get("spriteVersionNumber")
    if not isinstance(version, int) or isinstance(version, bool):
        raise ValueError("backup package manifest has an invalid sprite version")
    if require_v2 and version != 2:
        raise ValueError("package manifest is not V2")
    return manifest


def _load_backup_authority(
    backup: Path,
    pet_id: str,
    *,
    allow_legacy_backup: bool,
) -> dict[str, Any] | None:
    if backup.is_symlink() or not backup.is_dir():
        raise ValueError(f"backup is not a regular package directory: {backup}")
    sidecar_path = backup / BACKUP_SIDECAR
    if not sidecar_path.exists():
        if not allow_legacy_backup:
            raise ValueError(
                "sidecar-free backup requires explicit legacy opt-in (--allow-legacy-backup)"
            )
        return None
    if sidecar_path.is_symlink() or not sidecar_path.is_file():
        raise ValueError("backup integrity sidecar is not a regular file")
    sidecar = read_json(sidecar_path)
    files = sidecar.get("files")
    provenance = sidecar.get("provenance")
    if (
        sidecar.get("schema_version") != 1
        or sidecar.get("pet_id") != pet_id
        or not isinstance(files, dict)
        or set(files) != set(PACKAGE_FILES)
        or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) for value in files.values())
        or not isinstance(provenance, dict)
        or provenance.get("tool") != "petkit"
        or provenance.get("operation") not in {"install-displaced", "rollback-displaced"}
    ):
        raise ValueError("backup integrity sidecar is incomplete or has invalid provenance")
    return sidecar


def _verify_staged_backup(
    staging: Path,
    pet_id: str,
    authority: dict[str, Any] | None,
) -> dict[str, str]:
    hashes = _relative_file_hashes(staging, PACKAGE_FILES)
    _validate_package_manifest(staging, pet_id)
    if authority is not None and authority.get("files") != hashes:
        raise ValueError("staged backup no longer matches its recorded integrity hashes")
    return hashes


def _backup_order_key(path: Path) -> tuple[int, str]:
    sidecar_path = path / BACKUP_SIDECAR
    if sidecar_path.is_file() and not sidecar_path.is_symlink():
        try:
            created_at_ns = read_json(sidecar_path).get("created_at_ns")
        except ValueError:
            created_at_ns = None
        if isinstance(created_at_ns, int) and created_at_ns >= 0:
            return created_at_ns, path.name
    return path.stat().st_mtime_ns, path.name


def _record_post_commit(
    project_dir: Path,
    history_path: Path,
    payload: dict[str, Any],
    event: str,
) -> list[str]:
    warnings: list[str] = []
    try:
        atomic_write_json(history_path, payload)
    except Exception as exc:
        warnings.append(
            f"filesystem operation committed, but {history_path.name} could not be written: {exc}; "
            f"recovery paths: target={payload.get('target')!r}, backup={payload.get('backup')!r}, "
            f"restored_backup={payload.get('restored_backup')!r}, displaced_backup={payload.get('displaced_backup')!r}"
        )
    try:
        append_event(project_dir, event, payload)
    except Exception as exc:
        warnings.append(
            f"filesystem operation committed, but project history event {event!r} could not be appended: {exc}; "
            f"the operation record is {history_path}"
        )
    return warnings


def _cleanup_displaced(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        shutil.rmtree(path)
    except OSError:
        return str(path) if path.exists() else None
    return None


@project_mutation
def install_build(project_value: str | Path, target_root: Path, build_id: str | None = None) -> dict[str, Any]:
    project_dir, project = load_project(project_value)
    history_path = safe_project_directory(project_dir, "history", create=True) / "last-install.json"
    selected = build_id or project.get("accepted_build")
    if not selected:
        raise ValueError("project has no accepted build to install")
    if selected != project.get("accepted_build"):
        raise ValueError("only the project's accepted build may be installed")
    build_dir, record, _acceptance = _prepare_install_preflight(project_dir, project, selected)
    target_root, target = _resolve_install_target(project_dir, target_root, project["id"])
    target_root.mkdir(parents=True, exist_ok=True)
    if target_root.is_symlink():
        raise ValueError(f"install root must not be a symbolic link: {target_root}")
    backup_root = safe_project_directory(project_dir, "backups/installed", create=True)
    lock_path = target_root / f".{project['id']}.petkit-writer.lock"
    with file_writer_lock(lock_path):
        _resolved_root, target = _resolve_install_target(project_dir, target_root, project["id"])
        staging = target_root / f".{project['id']}.install-{uuid.uuid4().hex}"
        displaced = target_root / f".{project['id']}.previous-{uuid.uuid4().hex}"
        backup_dir: Path | None = None
        try:
            _copy_package(build_dir, staging)
            _verify_installable_package(staging, project["id"], record)
            if target.exists():
                os.replace(target, displaced)
                created_at_ns = time.time_ns()
                backup_dir = backup_root / f"{created_at_ns:020d}-{uuid.uuid4().hex[:8]}"
                backup_hashes = _copy_installed_package(displaced, backup_dir)
                _validate_package_manifest(backup_dir, project["id"])
                _write_backup_sidecar(
                    backup_dir,
                    project["id"],
                    backup_hashes,
                    created_at_ns,
                    "install-displaced",
                )
            os.replace(staging, target)
        except Exception:
            if target.exists() and displaced.exists():
                shutil.rmtree(target, ignore_errors=True)
            if displaced.exists() and not target.exists():
                os.replace(displaced, target)
            shutil.rmtree(staging, ignore_errors=True)
            if backup_dir is not None and not displaced.exists():
                shutil.rmtree(backup_dir, ignore_errors=True)
            raise
        cleanup_pending = _cleanup_displaced(displaced)
    install_record = {
        "schema_version": 2,
        "installed_at": now_iso(),
        "build_id": selected,
        "target": str(target),
        "backup": str(backup_dir) if backup_dir else None,
        "spritesheet_sha256": record["spritesheet_sha256"],
        "pet_json_sha256": record["pet_json_sha256"],
        "cleanup_pending": cleanup_pending,
    }
    warnings = _record_post_commit(project_dir, history_path, install_record, "build-installed-v2")
    return {"ok": True, "committed": True, "post_commit_warnings": warnings, **install_record}


@project_mutation
def rollback_install(
    project_value: str | Path,
    target_root: Path,
    backup: Path | None = None,
    *,
    allow_legacy_backup: bool = False,
) -> dict[str, Any]:
    project_dir, project = load_project(project_value)
    history_path = safe_project_directory(project_dir, "history", create=True) / "last-rollback.json"
    backup_root = safe_project_directory(project_dir, "backups/installed", create=True)
    if backup is None:
        candidates = [
            path
            for path in backup_root.iterdir()
            if path.is_dir() and not path.is_symlink()
        ]
        if not candidates:
            raise ValueError(f"no installed-package backup exists for {project['id']}")
        backup = max(candidates, key=_backup_order_key)
    requested_backup = backup.expanduser().absolute()
    if requested_backup.is_symlink():
        raise ValueError("backup directory must not be symbolic")
    backup = requested_backup.resolve()
    if requested_backup.parent != backup_root.absolute() or backup.parent != backup_root.resolve():
        raise ValueError(f"backup must be a direct child of this project's installed backups: {backup_root}")
    backup_authority = _load_backup_authority(
        backup,
        project["id"],
        allow_legacy_backup=allow_legacy_backup,
    )
    target_root, target = _resolve_install_target(project_dir, target_root, project["id"])
    target_root.mkdir(parents=True, exist_ok=True)
    if target_root.is_symlink():
        raise ValueError(f"install root must not be a symbolic link: {target_root}")
    lock_path = target_root / f".{project['id']}.petkit-writer.lock"
    with file_writer_lock(lock_path):
        _resolved_root, target = _resolve_install_target(project_dir, target_root, project["id"])
        staging = target_root / f".{project['id']}.rollback-{uuid.uuid4().hex}"
        displaced = target_root / f".{project['id']}.pre-rollback-{uuid.uuid4().hex}"
        displaced_backup: Path | None = None
        try:
            _copy_installed_package(backup, staging)
            sidecar_path = backup / BACKUP_SIDECAR
            if backup_authority is None:
                if sidecar_path.exists():
                    raise ValueError("backup provenance changed while staging rollback")
            elif read_json(sidecar_path) != backup_authority:
                raise ValueError("backup integrity sidecar changed while staging rollback")
            backup_hashes = _verify_staged_backup(staging, project["id"], backup_authority)
            if target.exists():
                os.replace(target, displaced)
                created_at_ns = time.time_ns()
                displaced_backup = backup_root / f"{created_at_ns:020d}-{uuid.uuid4().hex[:8]}-pre-rollback"
                displaced_hashes = _copy_installed_package(displaced, displaced_backup)
                _validate_package_manifest(displaced_backup, project["id"])
                _write_backup_sidecar(
                    displaced_backup,
                    project["id"],
                    displaced_hashes,
                    created_at_ns,
                    "rollback-displaced",
                )
            os.replace(staging, target)
        except Exception:
            if target.exists() and displaced.exists():
                shutil.rmtree(target, ignore_errors=True)
            shutil.rmtree(staging, ignore_errors=True)
            if displaced.exists() and not target.exists():
                os.replace(displaced, target)
            if displaced_backup is not None and not displaced.exists():
                shutil.rmtree(displaced_backup, ignore_errors=True)
            raise
        cleanup_pending = _cleanup_displaced(displaced)
    record = {
        "schema_version": 2,
        "rolled_back_at": now_iso(),
        "target": str(target),
        "restored_backup": str(backup),
        "displaced_backup": str(displaced_backup) if displaced_backup else None,
        "spritesheet_sha256": backup_hashes["spritesheet.webp"],
        "pet_json_sha256": backup_hashes["pet.json"],
        "cleanup_pending": cleanup_pending,
    }
    warnings = _record_post_commit(project_dir, history_path, record, "install-rolled-back")
    return {"ok": True, "committed": True, "post_commit_warnings": warnings, **record}
