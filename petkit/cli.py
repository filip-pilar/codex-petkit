from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import uuid
from functools import wraps
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from petkit.build import (
    accept_build,
    build_project,
    install_build,
    look_basis_fingerprint,
    preflight_phase,
    recorded_project_file,
    review_directions,
    rollback_install,
)
from petkit.contract import Contract, load_contract
from petkit.imageops import (
    _atomic_save,
    compare_atlases,
    extract_atlas_frames,
    extract_row_strip,
    inspect_frames,
    make_contact_sheet,
    mirror_state,
    normalize_frame,
    render_previews,
    render_state_preview,
    validate_atlas,
)
from petkit.project import (
    append_event,
    approve_identity,
    atomic_write_json,
    create_variant,
    ensure_tree_has_no_symlinks,
    init_project,
    load_project,
    now_iso,
    plan_edit,
    project_path,
    project_writer_lock,
    read_json,
    save_project,
    safe_project_directory,
    sha256_file,
    slugify,
    source_image_files,
    upgrade_project,
)
from petkit.v2 import validate_mechanics, validate_v2


def emit(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=False))


def path_arg(value: str) -> Path:
    return Path(value).expanduser()


def locked_project_command(function: Any) -> Any:
    @wraps(function)
    def wrapped(args: argparse.Namespace) -> Any:
        project_dir = project_path(args.project)
        with project_writer_lock(project_dir):
            source_root = safe_project_directory(project_dir, "source")
            ensure_tree_has_no_symlinks(source_root, boundary=project_dir)
            history_root = safe_project_directory(project_dir, "history")
            ensure_tree_has_no_symlinks(history_root, boundary=project_dir)
            qa_root = safe_project_directory(project_dir, "qa")
            ensure_tree_has_no_symlinks(qa_root, boundary=project_dir)
            return function(args)

    return wrapped


def _state_and_project(project_value: str, state_id: str):
    project_dir, project = load_project(project_value)
    contract = load_contract(2)
    state = contract.state(state_id)
    return project_dir, project, contract, state


def _ordered_frame_files(
    directory: Path,
    expected_count: int,
    *,
    boundary: Path,
    label: str,
    allow_row_manifest: bool = False,
) -> list[Path]:
    ensure_tree_has_no_symlinks(directory, boundary=boundary)
    entries = sorted(directory.iterdir())
    image_entries = [entry for entry in entries if entry.suffix.lower() in {".png", ".webp"}]
    if any(
        not entry.is_file()
        or (
            entry.suffix.lower() not in {".png", ".webp"}
            and not (allow_row_manifest and entry.name == "row-manifest.json")
        )
        for entry in entries
    ):
        raise ValueError(f"{label} contains an irregular or unsupported descendant")
    ordered: list[Path] = []
    for index in range(expected_count):
        stem = f"{index:02d}"
        candidates = [entry for entry in image_entries if entry.stem == stem]
        if len(candidates) != 1:
            if len(candidates) > 1:
                raise ValueError(f"{label} has ambiguous PNG/WebP files for frame {stem}")
            raise ValueError(f"{label} is missing frame {stem}")
        ordered.append(candidates[0])
    if len(image_entries) != expected_count:
        raise ValueError(f"{label} requires exactly {expected_count} numbered image files")
    return ordered


def _validate_frame_images(files: list[Path], contract: Contract, *, label: str) -> None:
    expected_size = (contract.cell_width, contract.cell_height)
    expected_formats = {".png": "PNG", ".webp": "WEBP"}
    for path in files:
        try:
            with Image.open(path) as opened:
                actual_format = opened.format
                actual_size = opened.size
                opened.load()
        except OSError as exc:
            raise ValueError(f"{label} contains an unreadable image: {path.name}") from exc
        expected_format = expected_formats[path.suffix.lower()]
        if actual_format != expected_format:
            raise ValueError(
                f"{label} frame {path.name} is {actual_format or 'unknown'} data, not {expected_format}"
            )
        if actual_size != expected_size:
            raise ValueError(
                f"{label} frame {path.name} has wrong dimensions: "
                f"expected {expected_size[0]}x{expected_size[1]}, got {actual_size[0]}x{actual_size[1]}"
            )


def _frame_path_for_index(project_dir: Path, state_id: str, frame_count: int, index: int) -> Path:
    frames_root = safe_project_directory(project_dir, "source/frames")
    state_dir = frames_root / state_id
    files = _ordered_frame_files(
        state_dir,
        frame_count,
        boundary=project_dir,
        label=f"source state {state_id}",
        allow_row_manifest=True,
    )
    return files[index]


def _backup_state(project_dir: Path, state_id: str, contract: Contract) -> Path | None:
    frames_root = safe_project_directory(project_dir, "source/frames")
    state_dir = frames_root / state_id
    if not state_dir.is_dir():
        return None
    state = contract.state(state_id)
    files = _ordered_frame_files(
        state_dir,
        state.frame_count,
        boundary=project_dir,
        label=f"source state {state_id}",
        allow_row_manifest=True,
    )
    _validate_frame_images(files, contract, label=f"source state {state_id}")
    timestamp = now_iso().replace(":", "").replace("-", "") + f"-{uuid.uuid4().hex[:8]}"
    backup_root = safe_project_directory(project_dir, "history/row-backups", create=True)
    backup = backup_root / f"{timestamp}-{state_id}"
    backup.mkdir(exist_ok=False)
    try:
        for source in files:
            shutil.copy2(source, backup / source.name)
    except Exception:
        shutil.rmtree(backup, ignore_errors=True)
        raise
    return backup


def _replace_state_dir(project_dir: Path, state_id: str, prepared: Path) -> None:
    destination = safe_project_directory(project_dir, "source/frames") / state_id
    if destination.is_symlink():
        raise ValueError(f"source state directory must not be symbolic: {destination}")
    displaced = destination.parent / f".{state_id}.previous"
    if displaced.exists():
        shutil.rmtree(displaced)
    if destination.exists():
        os.replace(destination, displaced)
    try:
        os.replace(prepared, destination)
        if displaced.exists():
            shutil.rmtree(displaced)
    except Exception:
        if displaced.exists() and not destination.exists():
            os.replace(displaced, destination)
        raise


def cmd_contract(args: argparse.Namespace) -> None:
    emit(load_contract(args.version).raw)


def cmd_init(args: argparse.Namespace) -> None:
    destination = init_project(
        root=args.root,
        pet_id=args.id,
        display_name=args.name,
        description=args.description,
        concept=args.concept,
        style=args.style,
        references=args.reference,
        chroma_key=args.chroma_key,
        chroma_threshold=args.chroma_threshold,
    )
    emit({"ok": True, "project": str(destination)})


def cmd_approve_identity(args: argparse.Namespace) -> None:
    project = approve_identity(args.project, args.image)
    emit(
        {
            "ok": True,
            "id": project["id"],
            "canonical_reference": project["identity"]["canonical_reference"],
            "post_commit_warnings": project.get("post_commit_warnings", []),
        }
    )


def _generation_status(project_dir: Path, project: dict[str, Any], contract: Contract) -> dict[str, Any]:
    frames_root = safe_project_directory(project_dir, "source/frames")
    states: dict[str, Any] = {}
    for state in contract.states:
        row_metadata = project["generation"].get("row_sources", {}).get(state.id, {})
        relative_row = row_metadata.get("path") if isinstance(row_metadata, dict) else None
        row_source = project_dir / relative_row if isinstance(relative_row, str) else None
        frame_dir = frames_root / state.id
        files = source_image_files(frame_dir, boundary=project_dir)
        states[state.id] = {
            "expected_frames": state.frame_count,
            "frame_count": len(files),
            "complete": len(files) == state.frame_count,
            "row_source": str(row_source) if row_source is not None and row_source.is_file() else None,
        }
    candidates: list[dict[str, Any]] = []
    builds_dir = safe_project_directory(project_dir, "builds")
    candidate_dirs = sorted(builds_dir.glob("build-*")) if builds_dir.is_dir() else []
    for build_dir in candidate_dirs:
        record_path = build_dir / "build.json"
        if not record_path.is_file():
            continue
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            isinstance(record, dict)
            and record.get("pet_id") == project["id"]
            and record.get("build_kind") == "candidate"
        ):
            candidates.append(
                {
                    "build_id": record.get("build_id"),
                    "created_at": record.get("created_at"),
                    "previous_build": record.get("previous_build"),
                }
            )
    preflight = {
        phase: preflight_phase(project_dir, project, phase)
        for phase in ("build", "review", "accept", "install")
    }
    blockers = [
        {**blocker, "phase": phase}
        for phase, result in preflight.items()
        for blocker in result["blockers"]
    ]
    return {
        "ok": True,
        "project": str(project_dir),
        "id": project["id"],
        "status": project["status"],
        "identity_approved": bool(project["identity"].get("approved")),
        "canonical_reference": project["identity"].get("canonical_reference"),
        "current_build": project.get("current_build"),
        "accepted_build": project.get("accepted_build"),
        "candidate_builds": candidates,
        "preflight": preflight,
        "blockers": blockers,
        "states": states,
        "look_gates": {
            "mechanics": isinstance(project.get("look", {}).get("mechanics"), dict),
            "cardinals_approved": project.get("look", {}).get("cardinals", {}).get("approved") is True
            if isinstance(project.get("look", {}).get("cardinals"), dict)
            else False,
            "row_9_approved": project.get("look", {}).get("row_9_approved") is True,
        },
        "ready_to_build": preflight["build"]["ok"],
    }


def cmd_status(args: argparse.Namespace) -> None:
    project_dir, project = load_project(args.project)
    emit(_generation_status(project_dir, project, load_contract(2)))


def _create_guide(path: Path, state_id: str, frame_count: int, contract: Contract, chroma_key: str) -> None:
    from petkit.imageops import parse_hex_color

    key = parse_hex_color(chroma_key)
    width = frame_count * contract.cell_width
    image = Image.new("RGB", (width, contract.cell_height), key)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    line = tuple(255 - value for value in key)
    for index in range(frame_count):
        left = index * contract.cell_width
        draw.rectangle((left + 2, 2, left + contract.cell_width - 3, contract.cell_height - 3), outline=line, width=2)
        draw.text((left + 8, 8), f"{index + 1}", fill=line, font=font)
    draw.text((8, contract.cell_height - 20), f"layout only: {state_id} — {frame_count} separated poses", fill=line, font=font)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG")


@locked_project_command
def cmd_guides(args: argparse.Namespace) -> None:
    project_dir, project = load_project(args.project)
    contract = load_contract(2)
    output = safe_project_directory(project_dir, "qa/layout-guides", create=True)
    paths = {}
    for state in contract.states:
        path = output / f"{state.id}.png"
        _create_guide(path, state.id, state.frame_count, contract, project["generation"]["chroma_key"])
        paths[state.id] = str(path)
    emit({"ok": True, "layout_guides": paths})


@locked_project_command
def cmd_ingest_row(args: argparse.Namespace) -> None:
    project_dir, project, contract, state = _state_and_project(args.project, args.state)
    if not project["identity"].get("approved"):
        raise ValueError("approve the canonical identity before ingesting animation rows")
    if state.id == "look-a":
        if not isinstance(project.get("look", {}).get("cardinals"), dict) or project["look"]["cardinals"].get("approved") is not True:
            raise ValueError("approve the four cardinal anchors before ingesting look row 9")
    row_9_basis: str | None = None
    if state.id == "look-b":
        approval = project.get("look", {}).get("row_9_approval")
        if project.get("look", {}).get("row_9_approved") is not True or not isinstance(approval, dict):
            raise ValueError("approve coherent look row 9 before ingesting look row 10")
        row_9_basis = look_basis_fingerprint(project_dir, project)
        if approval.get("basis_sha256") != row_9_basis:
            raise ValueError("row 9 approval is stale; approve the current row before ingesting look row 10")
    source = args.strip.expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"row strip does not exist: {source}")
    row_dir = project_dir / "source" / "rows" / state.id
    row_dir.mkdir(parents=True, exist_ok=True)
    versions = []
    for path in row_dir.glob("row-*.*"):
        try:
            versions.append(int(path.stem.split("-")[-1]))
        except ValueError:
            continue
    row_target = row_dir / f"row-{max(versions, default=0) + 1:04d}{source.suffix.lower() or '.png'}"
    shutil.copy2(source, row_target)
    temporary = Path(tempfile.mkdtemp(prefix=f".{state.id}.extract-", dir=project_dir / "source" / "frames"))
    try:
        result = extract_row_strip(
            row_target,
            temporary,
            state,
            contract,
            args.chroma_key or project["generation"]["chroma_key"],
            args.chroma_threshold if args.chroma_threshold is not None else float(project["generation"]["chroma_threshold"]),
            args.method,
        )
        backup = _backup_state(project_dir, state.id, contract)
        _replace_state_dir(project_dir, state.id, temporary)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    completed = set(project["generation"].get("completed_states", []))
    completed.add(state.id)
    project["generation"]["completed_states"] = [item.id for item in contract.states if item.id in completed]
    row_metadata = {
        "path": row_target.relative_to(project_dir).as_posix(),
        "sha256": sha256_file(row_target),
        "ingested_at": now_iso(),
        "method": result["method"],
    }
    if row_9_basis is not None:
        row_metadata["row_9_basis_sha256"] = row_9_basis
    project["generation"].setdefault("row_sources", {})[state.id] = row_metadata
    if state.id == "look-a":
        project["look"]["row_9_approved"] = False
        project["look"]["row_9_approval"] = None
    project["status"] = "generating"
    save_project(project_dir, project)
    append_event(
        project_dir,
        "row-ingested",
        {"state": state.id, "method": result["method"], "backup": str(backup) if backup else None},
    )
    emit({**result, "project": str(project_dir), "backup": str(backup) if backup else None})


def cmd_upgrade_project(args: argparse.Namespace) -> None:
    emit(upgrade_project(args.project))


@locked_project_command
def cmd_set_look_mechanics(args: argparse.Namespace) -> None:
    project_dir, project = load_project(args.project)
    source = args.file.expanduser().resolve()
    payload = read_json(source)
    contract = load_contract(2)
    validate_mechanics(payload, contract)
    target = project_dir / "source" / "look-mechanics.json"
    shutil.copy2(source, target)
    project["look"]["mechanics"] = {
        "path": target.relative_to(project_dir).as_posix(),
        "sha256": sha256_file(target),
        "recorded_at": now_iso(),
    }
    project["look"]["cardinals"] = None
    project["look"]["row_9_approved"] = False
    project["look"]["row_9_approval"] = None
    save_project(project_dir, project)
    append_event(project_dir, "look-mechanics-recorded", {"sha256": sha256_file(target)})
    emit({"ok": True, "project": str(project_dir), "mechanics": str(target)})


@locked_project_command
def cmd_approve_cardinals(args: argparse.Namespace) -> None:
    project_dir, project = load_project(args.project)
    if not isinstance(project.get("look", {}).get("mechanics"), dict):
        raise ValueError("record the 16-direction look mechanics before approving cardinals")
    recorded_project_file(
        project_dir,
        project["look"]["mechanics"],
        label="look mechanics",
    )
    source = args.strip.expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"cardinal strip does not exist: {source}")
    with Image.open(source) as opened:
        if opened.width < 4 or opened.height < 1:
            raise ValueError("cardinal strip is not a readable image")
    target_dir = project_dir / "source" / "cardinals"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"cardinals-{now_iso().replace(':', '').replace('-', '')}{source.suffix.lower() or '.png'}"
    shutil.copy2(source, target)
    project["look"]["cardinals"] = {
        "path": target.relative_to(project_dir).as_posix(),
        "sha256": sha256_file(target),
        "order": [0, 90, 180, 270],
        "approved": True,
        "approved_at": now_iso(),
        "review_note": args.review_note.strip(),
    }
    project["look"]["row_9_approved"] = False
    project["look"]["row_9_approval"] = None
    save_project(project_dir, project)
    append_event(project_dir, "cardinals-approved", {"path": str(target), "review_note": args.review_note.strip()})
    emit({"ok": True, "project": str(project_dir), "cardinals": str(target)})


@locked_project_command
def cmd_approve_look_row_9(args: argparse.Namespace) -> None:
    project_dir, project = load_project(args.project)
    metadata = project["generation"].get("row_sources", {}).get("look-a")
    if not args.review_note.strip():
        raise ValueError("row 9 approval requires a review note")
    _row_path, row_hash = recorded_project_file(
        project_dir,
        metadata,
        label="look-a source row",
    )
    basis_hash = look_basis_fingerprint(project_dir, project)
    project["look"]["row_9_approved"] = True
    project["look"]["row_9_approval"] = {
        "approved_at": now_iso(),
        "row_sha256": row_hash,
        "basis_sha256": basis_hash,
        "review_note": args.review_note.strip(),
    }
    save_project(project_dir, project)
    append_event(project_dir, "look-row-9-approved", project["look"]["row_9_approval"])
    emit({"ok": True, "project": str(project_dir), **project["look"]["row_9_approval"]})


@locked_project_command
def cmd_scale_look_row_source(args: argparse.Namespace) -> None:
    project_dir, project, contract, state = _state_and_project(args.project, args.state)
    if state not in contract.look_states:
        raise ValueError("whole-row source scaling is restricted to V2 look-a and look-b")
    if not 0.8 <= args.factor_y <= 1.25:
        raise ValueError("look-row vertical scale factor must be between 0.8 and 1.25")
    if not 0.8 <= args.factor_x <= 1.25:
        raise ValueError("look-row horizontal scale factor must be between 0.8 and 1.25")
    source = args.strip.expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"look row source does not exist: {source}")
    output = args.output.expanduser().resolve()
    if not output.is_relative_to(project_dir.resolve()):
        raise ValueError("scaled look-row source must be written inside its project")
    if output.exists():
        raise ValueError(f"refusing to overwrite an existing look-row source: {output}")
    with Image.open(source) as opened:
        image = opened.convert("RGB")
    resized = image.resize(
        (
            max(1, round(image.width * args.factor_x)),
            max(1, round(image.height * args.factor_y)),
        ),
        Image.Resampling.LANCZOS,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    resized.save(output, format="PNG")
    record = {
        "ok": True,
        "state": state.id,
        "source": str(source),
        "output": str(output),
        "factor_x": args.factor_x,
        "factor_y": args.factor_y,
        "width": resized.width,
        "height": resized.height,
        "sha256": sha256_file(output),
        "scope": "uniform complete-row axis scaling; no cell-specific edits",
    }
    append_event(project_dir, "look-row-source-scaled", record)
    emit(record)


@locked_project_command
def cmd_mirror_left(args: argparse.Namespace) -> None:
    project_dir, project, contract, left = _state_and_project(args.project, "running-left")
    right = contract.state("running-right")
    source_dir = project_dir / "source" / "frames" / right.id
    temporary = Path(tempfile.mkdtemp(prefix=".running-left.mirror-", dir=project_dir / "source" / "frames"))
    try:
        result = mirror_state(source_dir, temporary, right.frame_count)
        backup = _backup_state(project_dir, left.id, contract)
        _replace_state_dir(project_dir, left.id, temporary)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    completed = set(project["generation"].get("completed_states", []))
    completed.add(left.id)
    project["generation"]["completed_states"] = [item.id for item in contract.states if item.id in completed]
    project["generation"].setdefault("row_sources", {})[left.id] = {
        "derived_from": right.id,
        "derived_at": now_iso(),
        "decision_note": args.decision_note,
        "method": "mirror-preserving-temporal-order",
    }
    project["status"] = "generating"
    save_project(project_dir, project)
    append_event(project_dir, "row-mirrored", {"state": left.id, "source": right.id, "decision_note": args.decision_note})
    emit({**result, "state": left.id, "decision_note": args.decision_note, "backup": str(backup) if backup else None})


@locked_project_command
def cmd_replace_frame(args: argparse.Namespace) -> None:
    project_dir, project, contract, state = _state_and_project(args.project, args.state)
    if state in contract.look_states:
        raise ValueError("V2 look directions cannot be patched cell-by-cell; regenerate and ingest the complete eight-pose row")
    if args.index < 0 or args.index >= state.frame_count:
        raise ValueError(f"frame index must be between 0 and {state.frame_count - 1}")
    source = args.image.expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"replacement image does not exist: {source}")
    destination = _frame_path_for_index(project_dir, state.id, state.frame_count, args.index)
    timestamp = now_iso().replace(":", "").replace("-", "") + f"-{uuid.uuid4().hex[:8]}"
    backup_root = safe_project_directory(project_dir, "history/frame-backups", create=True)
    backup = backup_root / f"{timestamp}-{state.id}-{args.index:02d}{destination.suffix.lower()}"
    shutil.copy2(destination, backup)
    with Image.open(source) as opened:
        frame = normalize_frame(opened, contract, preserve_viewport=opened.size == (contract.cell_width, contract.cell_height))
    output_format = "WEBP" if destination.suffix.lower() == ".webp" else "PNG"
    _atomic_save(frame, destination, output_format)
    project["status"] = "generating"
    save_project(project_dir, project)
    append_event(project_dir, "frame-replaced", {"state": state.id, "index": args.index, "backup": str(backup)})
    emit({"ok": True, "frame": str(destination), "backup": str(backup)})


def _require_project_history_path(project_dir: Path, value: Path) -> Path:
    requested = value.expanduser().absolute()
    if requested.is_symlink():
        raise ValueError(f"backup must not be symbolic: {requested}")
    history = safe_project_directory(project_dir, "history").resolve()
    current = requested
    while current != history and current.is_relative_to(history):
        if current.is_symlink():
            raise ValueError(f"backup path must not contain symbolic links: {current}")
        current = current.parent
    resolved = requested.resolve()
    if not resolved.is_relative_to(history):
        raise ValueError(f"backup must be inside this project's history directory: {history}")
    return resolved


@locked_project_command
def cmd_restore_row(args: argparse.Namespace) -> None:
    project_dir, project, contract, state = _state_and_project(args.project, args.state)
    backup = _require_project_history_path(project_dir, args.backup)
    if not backup.is_dir():
        raise ValueError(f"row backup does not exist: {backup}")
    files = _ordered_frame_files(
        backup,
        state.frame_count,
        boundary=project_dir,
        label=f"row backup for {state.id}",
    )
    _validate_frame_images(files, contract, label=f"row backup for {state.id}")
    frames_root = safe_project_directory(project_dir, "source/frames")
    temporary = Path(tempfile.mkdtemp(prefix=f".{state.id}.restore-", dir=frames_root))
    try:
        for source in files:
            shutil.copy2(source, temporary / source.name)
        current_backup = _backup_state(project_dir, state.id, contract)
        _replace_state_dir(project_dir, state.id, temporary)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    project["status"] = "generating"
    project["generation"].setdefault("row_sources", {})[state.id] = {
        "restored_from_frames": backup.relative_to(project_dir).as_posix(),
        "restored_at": now_iso(),
        "method": "restore-exact-frames",
    }
    save_project(project_dir, project)
    append_event(
        project_dir,
        "row-restored",
        {"state": state.id, "restored_from": str(backup), "displaced_backup": str(current_backup) if current_backup else None},
    )
    emit(
        {
            "ok": True,
            "state": state.id,
            "restored_from": str(backup),
            "displaced_backup": str(current_backup) if current_backup else None,
        }
    )


@locked_project_command
def cmd_restore_frame(args: argparse.Namespace) -> None:
    project_dir, project, contract, state = _state_and_project(args.project, args.state)
    if state in contract.look_states:
        raise ValueError("V2 look directions cannot be restored cell-by-cell; restore or regenerate the complete row")
    if args.index < 0 or args.index >= state.frame_count:
        raise ValueError(f"frame index must be between 0 and {state.frame_count - 1}")
    backup = _require_project_history_path(project_dir, args.backup)
    if not backup.is_file():
        raise ValueError(f"frame backup does not exist: {backup}")
    destination = _frame_path_for_index(project_dir, state.id, state.frame_count, args.index)
    if backup.suffix.lower() != destination.suffix.lower():
        raise ValueError("frame backup extension does not match the current source frame extension")
    timestamp = now_iso().replace(":", "").replace("-", "") + f"-{uuid.uuid4().hex[:8]}"
    backup_root = safe_project_directory(project_dir, "history/frame-backups", create=True)
    displaced = backup_root / f"{timestamp}-{state.id}-{args.index:02d}{destination.suffix.lower()}"
    shutil.copy2(destination, displaced)
    with Image.open(backup) as opened:
        if opened.size != (contract.cell_width, contract.cell_height):
            raise ValueError(
                f"frame backup has wrong dimensions: expected {contract.cell_width}x{contract.cell_height}, got {opened.width}x{opened.height}"
            )
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=destination.suffix.lower(),
        dir=destination.parent,
    )
    os.close(fd)
    temporary_path = Path(temporary_name)
    try:
        shutil.copyfile(backup, temporary_path)
        os.replace(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)
    project["status"] = "generating"
    save_project(project_dir, project)
    append_event(
        project_dir,
        "frame-restored",
        {"state": state.id, "index": args.index, "restored_from": str(backup), "displaced_backup": str(displaced)},
    )
    emit({"ok": True, "frame": str(destination), "restored_from": str(backup), "displaced_backup": str(displaced)})


def cmd_extract_atlas(args: argparse.Namespace) -> None:
    contract = load_contract(args.version)
    emit(extract_atlas_frames(args.atlas.resolve(), args.output.resolve(), contract))


def cmd_validate(args: argparse.Namespace) -> None:
    load_contract(args.version)
    temporary_dir: Path | None = None
    output = args.output.resolve() if args.output else None
    if output is None:
        temporary_dir = Path(tempfile.mkdtemp(prefix="petkit-v2-validation-"))
        output = temporary_dir / "validation.json"
    try:
        report = validate_v2(args.atlas.resolve(), output, chroma_key=args.chroma_key)
        emit(report)
    finally:
        if temporary_dir is not None:
            shutil.rmtree(temporary_dir, ignore_errors=True)
    if not report["ok"]:
        raise SystemExit(1)


def cmd_inspect_frames(args: argparse.Namespace) -> None:
    report = inspect_frames(args.frames.resolve(), load_contract(args.version), args.min_used_pixels)
    if args.output:
        atomic_write_json(args.output.resolve(), report)
    emit(report)
    if not report["ok"]:
        raise SystemExit(1)


def cmd_contact_sheet(args: argparse.Namespace) -> None:
    emit(make_contact_sheet(args.atlas.resolve(), args.output.resolve(), load_contract(args.version), args.scale))


def cmd_previews(args: argparse.Namespace) -> None:
    emit(render_previews(args.frames.resolve(), args.output.resolve(), load_contract(args.version), args.scale))


@locked_project_command
def cmd_preview_state(args: argparse.Namespace) -> None:
    project_dir, project, contract, state = _state_and_project(args.project, args.state)
    output = (
        args.output.resolve()
        if args.output
        else safe_project_directory(project_dir, "qa/row-previews", create=True) / f"{state.id}.gif"
    )
    emit(render_state_preview(project_dir / "source" / "frames", output, state, contract, args.scale))


def cmd_build(args: argparse.Namespace) -> None:
    emit(build_project(args.project, draft=args.draft))


def cmd_plan_edit(args: argparse.Namespace) -> None:
    emit(plan_edit(args.project, args.mode, args.outcome, args.allow_state, args.invariant))


def cmd_accept(args: argparse.Namespace) -> None:
    emit(
        accept_build(
            args.project,
            args.build_id,
            confirm_visual_qa=args.confirm_visual_qa,
            review_note=args.review_note,
        )
    )


def cmd_review_directions(args: argparse.Namespace) -> None:
    emit(
        review_directions(
            args.project,
            args.build_id,
            direction_semantics=args.direction_semantics,
            blind_verdicts=args.blind_verdict,
            semantic_verdicts=args.semantic_verdict,
            independent_visual_qas=args.independent_visual_qa,
            continuity_override_note=args.continuity_override_note,
            inherit_direction_from=args.inherit_direction_from,
        )
    )


def cmd_compare(args: argparse.Namespace) -> None:
    emit(compare_atlases(args.before.resolve(), args.after.resolve(), load_contract(args.version)))


def cmd_variant(args: argparse.Namespace) -> None:
    destination = create_variant(args.project, args.root, args.id, args.name)
    emit({"ok": True, "variant": str(destination)})


def cmd_install(args: argparse.Namespace) -> None:
    emit(install_build(args.project, args.target_root, args.build_id))


def cmd_rollback(args: argparse.Namespace) -> None:
    emit(
        rollback_install(
            args.project,
            args.target_root,
            args.backup,
            allow_legacy_backup=args.allow_legacy_backup,
        )
    )


def cmd_import(args: argparse.Namespace) -> None:
    package = args.package.expanduser().resolve()
    manifest = read_json(package / "pet.json")
    missing = [field for field in ("id", "displayName", "description", "spriteVersionNumber", "spritesheetPath") if not manifest.get(field)]
    if missing:
        raise ValueError(f"package manifest is missing required fields: {', '.join(missing)}")
    spritesheet = (package / str(manifest["spritesheetPath"])).resolve()
    if not spritesheet.is_relative_to(package):
        raise ValueError("package spritesheetPath must remain inside the package directory")
    if manifest.get("spriteVersionNumber") != 2:
        raise ValueError("only V2 pet packages can be imported")
    contract = load_contract(2)
    validation_tmp = Path(tempfile.mkdtemp(prefix="petkit-import-validation-"))
    try:
        report = validate_v2(spritesheet, validation_tmp / "validation.json", chroma_key="#00FF00")
    finally:
        shutil.rmtree(validation_tmp, ignore_errors=True)
    if not report["ok"]:
        raise ValueError(f"cannot import invalid atlas: {len(report['errors'])} validation error(s)")
    pet_id = args.id or slugify(str(manifest.get("id") or package.name))
    import_root = args.root.expanduser().resolve()
    destination = import_root / pet_id
    if destination.exists():
        raise ValueError(f"pet project already exists: {destination}")
    staging_root = import_root / f".{pet_id}.import-{uuid.uuid4().hex}"
    staged_project: Path | None = None
    try:
        staged_project = init_project(
            staging_root,
            pet_id,
            str(manifest.get("displayName") or pet_id),
            str(manifest.get("description") or f"Imported Codex pet {pet_id}."),
            "Imported from an installed or packaged V2 atlas; original generation sources are unavailable.",
            "unknown-imported-style",
        )
        with project_writer_lock(staged_project):
            project_dir, project = load_project(staged_project)
            extract_atlas_frames(spritesheet, project_dir / "source" / "frames", contract)
            with Image.open(spritesheet) as opened:
                atlas = opened.convert("RGBA")
                for state_id in ("look-a", "look-b"):
                    state = contract.state(state_id)
                    row_dir = project_dir / "source" / "rows" / state_id
                    row_dir.mkdir(parents=True, exist_ok=True)
                    row = row_dir / "row-0001.png"
                    atlas.crop((0, state.row * contract.cell_height, contract.width, (state.row + 1) * contract.cell_height)).save(row)
                    project["generation"]["row_sources"][state_id] = {
                        "path": row.relative_to(project_dir).as_posix(),
                        "sha256": sha256_file(row),
                        "ingested_at": now_iso(),
                        "method": "import-exact-v2-row",
                    }
            shutil.copy2(spritesheet, project_dir / "references" / "original" / spritesheet.name)
            project["generation"]["completed_states"] = [state.id for state in contract.states]
            project["generation"]["recovery_import"] = {
                "source": str(package),
                "atlas_sha256": sha256_file(spritesheet),
                "baseline_mode": "repairable-recovery",
                "note": (
                    "Recovered pixels are repair inputs, not an unchanged fork proof; mechanics, cardinal, "
                    "row, build, and review gates remain unverified."
                ),
            }
            project["look"]["mechanics"] = None
            project["look"]["cardinals"] = None
            project["look"]["row_9_approved"] = False
            project["look"]["row_9_approval"] = None
            project["status"] = "generating"
            save_project(project_dir, project)
            append_event(project_dir, "package-imported", {"source": str(package)})
        import_root.mkdir(parents=True, exist_ok=True)
        os.replace(staged_project, destination)
        staged_project = None
    finally:
        if staged_project is not None:
            shutil.rmtree(staging_root, ignore_errors=True)
        elif staging_root.exists():
            shutil.rmtree(staging_root, ignore_errors=True)
    emit({"ok": True, "project": str(destination), "validation": report})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="petkit", description="Deterministic Codex Pet Workshop toolkit")
    subparsers = parser.add_subparsers(dest="command", required=True)

    command = subparsers.add_parser("contract", help="Print a machine-readable pet contract")
    command.add_argument("--version", type=int, default=2)
    command.set_defaults(func=cmd_contract)

    command = subparsers.add_parser("init", help="Create an editable pet project")
    command.add_argument("--root", type=path_arg, default=Path("pets"))
    command.add_argument("--id", required=True)
    command.add_argument("--name", required=True)
    command.add_argument("--description", required=True)
    command.add_argument("--concept", required=True)
    command.add_argument("--style", default="auto")
    command.add_argument("--reference", type=path_arg, action="append", default=[])
    command.add_argument("--chroma-key", default="#00FF00")
    command.add_argument("--chroma-threshold", type=float, default=96.0)
    command.set_defaults(func=cmd_init)

    command = subparsers.add_parser("approve-identity", help="Record the user-approved canonical identity image")
    command.add_argument("--project", required=True)
    command.add_argument("--image", type=path_arg, required=True)
    command.set_defaults(func=cmd_approve_identity)

    command = subparsers.add_parser("status", help="Report resumable project and row state")
    command.add_argument("--project", required=True)
    command.set_defaults(func=cmd_status)

    command = subparsers.add_parser("upgrade-project", help="One-way upgrade of a local workshop project to V2")
    command.add_argument("--project", required=True)
    command.set_defaults(func=cmd_upgrade_project)

    command = subparsers.add_parser("set-look-mechanics", help="Record the ordered 16-direction eye/head/body mechanics")
    command.add_argument("--project", required=True)
    command.add_argument("--file", type=path_arg, required=True)
    command.set_defaults(func=cmd_set_look_mechanics)

    command = subparsers.add_parser("approve-cardinals", help="Approve a coherent 000/090/180/270 cardinal anchor strip")
    command.add_argument("--project", required=True)
    command.add_argument("--strip", type=path_arg, required=True)
    command.add_argument("--review-note", required=True)
    command.set_defaults(func=cmd_approve_cardinals)

    command = subparsers.add_parser("approve-look-row-9", help="Approve the coherent first eight look directions before row 10")
    command.add_argument("--project", required=True)
    command.add_argument("--review-note", required=True)
    command.set_defaults(func=cmd_approve_look_row_9)

    command = subparsers.add_parser("scale-look-row-source", help="Uniformly correct complete-row look-source scale without cell patches")
    command.add_argument("--project", required=True)
    command.add_argument("--state", choices=("look-a", "look-b"), required=True)
    command.add_argument("--strip", type=path_arg, required=True)
    command.add_argument("--factor-x", type=float, default=1.0)
    command.add_argument("--factor-y", type=float, required=True)
    command.add_argument("--output", type=path_arg, required=True)
    command.set_defaults(func=cmd_scale_look_row_source)

    command = subparsers.add_parser("make-guides", help="Create deterministic row layout guides")
    command.add_argument("--project", required=True)
    command.set_defaults(func=cmd_guides)

    command = subparsers.add_parser("ingest-row", help="Extract one generated row strip into retained frames")
    command.add_argument("--project", required=True)
    command.add_argument("--state", required=True)
    command.add_argument("--strip", type=path_arg, required=True)
    command.add_argument(
        "--method",
        choices=("auto", "components", "slots", "stable-slots", "motion-components"),
        default="auto",
    )
    command.add_argument("--chroma-key")
    command.add_argument("--chroma-threshold", type=float)
    command.set_defaults(func=cmd_ingest_row)

    command = subparsers.add_parser("mirror-running-left", help="Derive left locomotion after a documented visual approval")
    command.add_argument("--project", required=True)
    command.add_argument("--decision-note", required=True)
    command.set_defaults(func=cmd_mirror_left)

    command = subparsers.add_parser("replace-frame", help="Replace exactly one retained frame with a reversible backup")
    command.add_argument("--project", required=True)
    command.add_argument("--state", required=True)
    command.add_argument("--index", type=int, required=True)
    command.add_argument("--image", type=path_arg, required=True)
    command.set_defaults(func=cmd_replace_frame)

    command = subparsers.add_parser("restore-row", help="Restore a row backup and preserve the displaced current row")
    command.add_argument("--project", required=True)
    command.add_argument("--state", required=True)
    command.add_argument("--backup", type=path_arg, required=True)
    command.set_defaults(func=cmd_restore_row)

    command = subparsers.add_parser("restore-frame", help="Restore one frame backup and preserve the displaced frame")
    command.add_argument("--project", required=True)
    command.add_argument("--state", required=True)
    command.add_argument("--index", type=int, required=True)
    command.add_argument("--backup", type=path_arg, required=True)
    command.set_defaults(func=cmd_restore_frame)

    command = subparsers.add_parser("extract-atlas", help="Extract an existing V2 atlas into exact source frames")
    command.add_argument("--atlas", type=path_arg, required=True)
    command.add_argument("--output", type=path_arg, required=True)
    command.add_argument("--version", type=int, default=2)
    command.set_defaults(func=cmd_extract_atlas)

    command = subparsers.add_parser("validate", help="Validate atlas geometry, transparency, and used cells")
    command.add_argument("--atlas", type=path_arg, required=True)
    command.add_argument("--output", type=path_arg)
    command.add_argument("--version", type=int, default=2)
    command.add_argument("--min-used-pixels", type=int, default=64)
    command.add_argument("--chroma-key", default="#00FF00")
    command.set_defaults(func=cmd_validate)

    command = subparsers.add_parser("inspect-frames", help="Measure retained frame geometry and duplicate motion")
    command.add_argument("--frames", type=path_arg, required=True)
    command.add_argument("--output", type=path_arg)
    command.add_argument("--version", type=int, default=2)
    command.add_argument("--min-used-pixels", type=int, default=64)
    command.set_defaults(func=cmd_inspect_frames)

    command = subparsers.add_parser("contact-sheet", help="Render a labeled atlas contact sheet")
    command.add_argument("--atlas", type=path_arg, required=True)
    command.add_argument("--output", type=path_arg, required=True)
    command.add_argument("--version", type=int, default=2)
    command.add_argument("--scale", type=float, default=0.5)
    command.set_defaults(func=cmd_contact_sheet)

    command = subparsers.add_parser("previews", help="Render per-state animated GIF previews")
    command.add_argument("--frames", type=path_arg, required=True)
    command.add_argument("--output", type=path_arg, required=True)
    command.add_argument("--version", type=int, default=2)
    command.add_argument("--scale", type=int, default=2)
    command.set_defaults(func=cmd_previews)

    command = subparsers.add_parser("preview-state", help="Render one completed project state for immediate visual QA")
    command.add_argument("--project", required=True)
    command.add_argument("--state", required=True)
    command.add_argument("--output", type=path_arg)
    command.add_argument("--scale", type=int, default=2)
    command.set_defaults(func=cmd_preview_state)

    command = subparsers.add_parser("build", help="Create an immutable validated build")
    command.add_argument("--project", required=True)
    command.add_argument(
        "--draft",
        action="store_true",
        help="Create a mechanically validated candidate that cannot be reviewed, accepted, or installed",
    )
    command.set_defaults(func=cmd_build)

    command = subparsers.add_parser("plan-edit", help="Record and enforce the allowed scope of an edit")
    command.add_argument("--project", required=True)
    command.add_argument("--mode", choices=("deterministic", "generative", "variant"), required=True)
    command.add_argument("--outcome", required=True)
    command.add_argument("--allow-state", action="append", default=[])
    command.add_argument("--invariant", action="append", default=[])
    command.set_defaults(func=cmd_plan_edit)

    command = subparsers.add_parser("accept", help="Mark a reviewed build accepted")
    command.add_argument("--project", required=True)
    command.add_argument("--build-id")
    command.add_argument("--confirm-visual-qa", action="store_true")
    command.add_argument("--review-note", required=True)
    command.set_defaults(func=cmd_accept)

    command = subparsers.add_parser("review-directions", help="Combine blind V2 direction reviews and record independent semantic/visual QA")
    command.add_argument("--project", required=True)
    command.add_argument("--build-id", required=True)
    command.add_argument("--direction-semantics", type=path_arg)
    command.add_argument("--blind-verdict", type=path_arg, action="append", default=[])
    command.add_argument("--semantic-verdict", type=path_arg, action="append", required=True)
    command.add_argument("--independent-visual-qa", type=path_arg, action="append", required=True)
    command.add_argument("--continuity-override-note", default="")
    command.add_argument("--inherit-direction-from", help="Reuse a reviewed parent direction scope when neutral/look pixels and build inputs are unchanged")
    command.set_defaults(func=cmd_review_directions)

    command = subparsers.add_parser("compare", help="Compare two atlases at frame granularity")
    command.add_argument("--before", type=path_arg, required=True)
    command.add_argument("--after", type=path_arg, required=True)
    command.add_argument("--version", type=int, default=2)
    command.set_defaults(func=cmd_compare)

    command = subparsers.add_parser("variant", help="Create a linked, isolated variant project")
    command.add_argument("--project", required=True)
    command.add_argument("--root", type=path_arg, default=Path("pets"))
    command.add_argument("--id", required=True)
    command.add_argument("--name", required=True)
    command.set_defaults(func=cmd_variant)

    command = subparsers.add_parser("import-package", help="Recover editable frames from an existing V2 package")
    command.add_argument("--package", type=path_arg, required=True)
    command.add_argument("--root", type=path_arg, default=Path("pets"))
    command.add_argument("--id")
    command.set_defaults(func=cmd_import)

    command = subparsers.add_parser("install", help="Explicitly install an accepted build with backup")
    command.add_argument("--project", required=True)
    command.add_argument("--target-root", type=path_arg, required=True)
    command.add_argument("--build-id")
    command.set_defaults(func=cmd_install)

    command = subparsers.add_parser("rollback", help="Explicitly restore an installed-package backup")
    command.add_argument("--project", required=True)
    command.add_argument("--target-root", type=path_arg, required=True)
    command.add_argument("--backup", type=path_arg)
    command.add_argument(
        "--allow-legacy-backup",
        action="store_true",
        help="explicitly permit a structurally valid backup created before PetKit integrity sidecars",
    )
    command.set_defaults(func=cmd_rollback)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except (ValueError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
