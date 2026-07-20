from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from petkit.contract import Contract, load_contract


PROJECT_FILE = "pet-project.json"
IDENTITY_FILE = "identity.md"


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    if not slug:
        raise ValueError("pet id must contain at least one ASCII letter or number")
    return slug


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"missing JSON file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object in {path}")
    return value


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, sort_keys=False) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def append_event(project_dir: Path, event: str, details: dict[str, Any]) -> None:
    path = project_dir / "history" / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"at": now_iso(), "event": event, **details}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def project_path(value: str | Path) -> Path:
    path = Path(value).expanduser().resolve()
    if path.is_file() and path.name == PROJECT_FILE:
        path = path.parent
    if not (path / PROJECT_FILE).is_file():
        raise ValueError(f"not an editable pet project: {path}")
    return path


def load_project(value: str | Path) -> tuple[Path, dict[str, Any]]:
    path = project_path(value)
    project = read_json(path / PROJECT_FILE)
    validate_project(project)
    return path, project


def validate_project(project: dict[str, Any]) -> None:
    required = {
        "schema_version",
        "id",
        "display_name",
        "description",
        "contract_version",
        "status",
        "created_at",
        "updated_at",
        "identity",
        "generation",
    }
    missing = sorted(required - project.keys())
    if missing:
        raise ValueError(f"project metadata is missing: {', '.join(missing)}")
    if project["schema_version"] != 1:
        raise ValueError(f"unsupported project schema: {project['schema_version']}")
    if slugify(str(project["id"])) != project["id"]:
        raise ValueError("project id is not a normalized slug")
    if project["contract_version"] != 2:
        raise ValueError("this workshop supports only pet contract version 2; run upgrade-project for an older local project")
    load_contract(2)
    if not isinstance(project["identity"], dict) or not isinstance(project["generation"], dict):
        raise ValueError("project identity and generation fields must be objects")
    if not isinstance(project.get("look"), dict):
        raise ValueError("project look metadata must be an object")


def save_project(project_dir: Path, project: dict[str, Any]) -> None:
    project["updated_at"] = now_iso()
    validate_project(project)
    atomic_write_json(project_dir / PROJECT_FILE, project)


def init_project(
    root: Path,
    pet_id: str,
    display_name: str,
    description: str,
    concept: str,
    style: str,
    references: Iterable[Path] = (),
    chroma_key: str = "#00FF00",
    chroma_threshold: float = 96.0,
) -> Path:
    normalized_id = slugify(pet_id)
    if normalized_id != pet_id:
        raise ValueError(f"pet id must be the normalized slug {normalized_id!r}")
    root = root.expanduser().resolve()
    destination = root / normalized_id
    if destination.exists():
        raise ValueError(f"pet project already exists: {destination}")
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", chroma_key):
        raise ValueError("chroma key must be a six-digit hex colour such as #00FF00")
    if not 0 <= chroma_threshold <= 441.7:
        raise ValueError("chroma threshold must be between 0 and 441.7")
    resolved_references: list[Path] = []
    for reference in references:
        source = reference.expanduser().resolve()
        if not source.is_file():
            raise ValueError(f"reference image does not exist: {source}")
        resolved_references.append(source)
    created_at = now_iso()
    for relative in (
        "references/original",
        "references/candidates",
        "references/approved",
        "source/rows",
        "source/frames",
        "builds",
        "history",
        "backups/installed",
        "qa",
    ):
        (destination / relative).mkdir(parents=True, exist_ok=True)

    copied_references: list[str] = []
    for index, source in enumerate(resolved_references, start=1):
        suffix = source.suffix.lower() or ".png"
        target = destination / "references" / "original" / f"reference-{index:02d}{suffix}"
        shutil.copy2(source, target)
        copied_references.append(target.relative_to(destination).as_posix())

    project: dict[str, Any] = {
        "schema_version": 1,
        "id": normalized_id,
        "display_name": display_name.strip() or normalized_id,
        "description": description.strip() or f"A custom Codex pet named {display_name.strip() or normalized_id}.",
        "contract_version": 2,
        "status": "brief",
        "created_at": created_at,
        "updated_at": created_at,
        "parent_id": None,
        "identity": {
            "concept": concept.strip(),
            "style": style.strip() or "auto",
            "approved": False,
            "approved_at": None,
            "canonical_reference": None,
            "supporting_references": copied_references,
        },
        "generation": {
            "chroma_key": chroma_key.upper(),
            "chroma_threshold": chroma_threshold,
            "completed_states": [],
            "row_sources": {},
        },
        "look": {
            "mechanics": None,
            "cardinals": None,
            "row_9_approved": False,
            "row_9_approval": None,
        },
        "current_build": None,
        "accepted_build": None,
        "active_edit": None,
    }
    save_project(destination, project)
    (destination / IDENTITY_FILE).write_text(
        "# Identity and art direction\n\n"
        f"## Concept\n\n{concept.strip() or 'To be established.'}\n\n"
        f"## Style\n\n{style.strip() or 'Auto; infer from the approved identity reference.'}\n\n"
        "## Invariants\n\n"
        "Record the silhouette, proportions, face, palette, materials, markings, props, and avoidances that every state must preserve.\n",
        encoding="utf-8",
    )
    append_event(destination, "project-created", {"id": normalized_id})
    return destination


def approve_identity(project_value: str | Path, canonical_reference: Path) -> dict[str, Any]:
    project_dir, project = load_project(project_value)
    source = canonical_reference.expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"canonical identity image does not exist: {source}")
    suffix = source.suffix.lower() or ".png"
    target = project_dir / "references" / "approved" / f"canonical-base{suffix}"
    if source != target.resolve():
        shutil.copy2(source, target)
    relative = target.relative_to(project_dir).as_posix()
    approved_at = now_iso()
    project["identity"].update(
        {
            "approved": True,
            "approved_at": approved_at,
            "canonical_reference": relative,
            "canonical_sha256": sha256_file(target),
        }
    )
    project["status"] = "identity-approved"
    save_project(project_dir, project)
    append_event(project_dir, "identity-approved", {"reference": relative})
    return project


def next_build_id(project_dir: Path) -> str:
    numbers = []
    for path in (project_dir / "builds").glob("build-*"):
        match = re.fullmatch(r"build-(\d{4})", path.name)
        if match:
            numbers.append(int(match.group(1)))
    return f"build-{max(numbers, default=0) + 1:04d}"


def current_build_dir(project_dir: Path, project: dict[str, Any]) -> Path:
    build_id = project.get("current_build")
    if not build_id:
        raise ValueError(f"project has no current build: {project_dir}")
    if not isinstance(build_id, str) or not re.fullmatch(r"build-\d{4}", build_id):
        raise ValueError(f"project has an invalid current build id: {build_id!r}")
    path = project_dir / "builds" / str(build_id)
    if not path.is_dir():
        raise ValueError(f"current build is missing: {path}")
    return path


def create_variant(source_value: str | Path, root: Path, new_id: str, display_name: str) -> Path:
    source_dir, source = load_project(source_value)
    normalized_id = slugify(new_id)
    if normalized_id != new_id:
        raise ValueError(f"variant id must be the normalized slug {normalized_id!r}")
    root = root.expanduser().resolve()
    destination = root / normalized_id
    if destination.exists():
        raise ValueError(f"variant project already exists: {destination}")
    root.mkdir(parents=True, exist_ok=True)
    staging = root / f".{normalized_id}.variant-{uuid.uuid4().hex}"
    try:
        staging.mkdir(parents=False, exist_ok=False)
        shutil.copytree(source_dir / "references", staging / "references")
        shutil.copytree(source_dir / "source", staging / "source")
        for relative in ("builds", "history", "backups/installed", "qa"):
            (staging / relative).mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_dir / IDENTITY_FILE, staging / IDENTITY_FILE)
        created_at = now_iso()
        variant = json.loads(json.dumps(source))
        variant.update(
            {
                "id": normalized_id,
                "display_name": display_name.strip() or normalized_id,
                "parent_id": source["id"],
                "created_at": created_at,
                "updated_at": created_at,
                "status": "identity-approved" if source["identity"].get("approved") else "brief",
                "current_build": None,
                "accepted_build": None,
                "active_edit": None,
            }
        )
        save_project(staging, variant)
        append_event(staging, "variant-created", {"parent_id": source["id"]})
        os.replace(staging, destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return destination


def plan_edit(
    project_value: str | Path,
    mode: str,
    outcome: str,
    allowed_states: Iterable[str],
    invariants: Iterable[str] = (),
) -> dict[str, Any]:
    project_dir, project = load_project(project_value)
    if mode not in {"deterministic", "generative", "variant"}:
        raise ValueError("edit mode must be deterministic, generative, or variant")
    if not outcome.strip():
        raise ValueError("edit outcome must not be empty")
    baseline = project.get("current_build")
    if not baseline:
        raise ValueError("create a baseline build before planning an edit")
    contract = contract_for_project(project)
    allowed = list(dict.fromkeys(allowed_states))
    for state_id in allowed:
        contract.state(state_id)
    edit_id = f"edit-{now_iso().replace(':', '').replace('-', '')}-{uuid.uuid4().hex[:8]}"
    record = {
        "schema_version": 1,
        "edit_id": edit_id,
        "planned_at": now_iso(),
        "mode": mode,
        "outcome": outcome.strip(),
        "initial_baseline_build": baseline,
        "comparison_build": baseline,
        "latest_build": None,
        "allowed_states": allowed,
        "invariants": [item.strip() for item in invariants if item.strip()],
    }
    relative = Path("history") / "edit-scopes" / f"{edit_id}.json"
    atomic_write_json(project_dir / relative, record)
    project["active_edit"] = {**record, "record": relative.as_posix()}
    save_project(project_dir, project)
    append_event(
        project_dir,
        "edit-planned",
        {"edit_id": edit_id, "mode": mode, "baseline_build": baseline, "allowed_states": allowed},
    )
    return {"ok": True, "project": str(project_dir), **project["active_edit"]}


def contract_for_project(project: dict[str, Any]) -> Contract:
    return load_contract(2)


def upgrade_project(project_value: str | Path) -> dict[str, Any]:
    """One-way metadata migration for projects created by this workshop before V2."""
    path = project_path(project_value)
    project = read_json(path / PROJECT_FILE)
    previous = project.get("contract_version")
    if previous == 2:
        validate_project(project)
        return {"ok": True, "project": str(path), "already_v2": True}
    if previous != 1:
        raise ValueError(f"cannot upgrade unsupported contract version: {previous!r}")
    project["contract_version"] = 2
    project["look"] = {
        "mechanics": None,
        "cardinals": None,
        "row_9_approved": False,
        "row_9_approval": None,
    }
    project["status"] = "generating"
    project["active_edit"] = None
    save_project(path, project)
    append_event(path, "project-upgraded-v2", {"from_contract_version": 1, "preserved_builds": True})
    return {"ok": True, "project": str(path), "from_contract_version": 1, "contract_version": 2}
