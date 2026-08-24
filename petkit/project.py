from __future__ import annotations

import hashlib
import fcntl
import json
import os
import re
import shutil
import stat
import tempfile
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import lru_cache, wraps
from pathlib import Path
from typing import Any, Iterable, Iterator

from jsonschema import Draft202012Validator, FormatChecker

from petkit.contract import load_contract


PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_FILE = "pet-project.json"
IDENTITY_FILE = "identity.md"
PROJECT_SCHEMA_FILE = PACKAGE_ROOT / "schemas" / "project.schema.json"
PROJECT_FORMAT_CHECKER = FormatChecker()
IMAGE_SUFFIXES = {".png", ".webp"}
_LOCK_STATE = threading.local()
RECOVERY_DIR = ".petkit-recovery"


class TransactionRecoveryError(RuntimeError):
    """An operation was interrupted and at least one durable path could not be reconciled."""


def _error_description(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"


def transaction_trace(phase: str, **details: Any) -> None:
    """No-op phase hook used by deterministic transaction fault-injection tests."""

    del phase, details


def recover_operation_path(
    path: Path,
    label: str,
    *,
    quarantine: Path | None = None,
    quarantine_first: bool = False,
    defer_cancellation: bool = False,
) -> list[str]:
    """Idempotently remove both known locations for one operation-owned path."""

    errors: list[str] = []
    cancellation: BaseException | None = None

    def exists(candidate: Path) -> bool:
        return os.path.lexists(candidate)

    def remove(candidate: Path) -> None:
        if candidate.is_symlink() or not candidate.is_dir():
            candidate.unlink()
        else:
            shutil.rmtree(candidate)

    def record_cancellation(error: BaseException) -> None:
        nonlocal cancellation
        if not isinstance(error, Exception) and cancellation is None:
            cancellation = error

    if not exists(path) and (quarantine is None or not exists(quarantine)):
        return []

    if quarantine is not None and quarantine_first and exists(path):
        if exists(quarantine):
            try:
                remove(quarantine)
            except BaseException as cleanup_error:
                record_cancellation(cleanup_error)
        if not exists(quarantine):
            try:
                os.replace(path, quarantine)
            except BaseException as recovery_error:
                record_cancellation(recovery_error)
                if exists(path) and not exists(quarantine):
                    errors.append(
                        f"{label} could not be moved from {path} to its registered recovery path "
                        f"{quarantine}: {_error_description(recovery_error)}"
                    )

    for active in (path, quarantine):
        if active is None or not exists(active):
            continue
        try:
            remove(active)
        except BaseException as cleanup_error:
            record_cancellation(cleanup_error)
            if not exists(active):
                continue
            if active == path and quarantine is not None and not exists(quarantine):
                try:
                    os.replace(path, quarantine)
                except BaseException as quarantine_error:
                    record_cancellation(quarantine_error)
                    if not exists(path) and exists(quarantine):
                        continue
                    errors.append(
                        f"{label} cleanup failed at {path}: {_error_description(cleanup_error)}; "
                        f"move to registered recovery path {quarantine} also failed: "
                        f"{_error_description(quarantine_error)}"
                    )
                    continue
                active = quarantine
                try:
                    remove(active)
                except BaseException as recovery_cleanup_error:
                    record_cancellation(recovery_cleanup_error)
                    if not exists(active):
                        continue
                    errors.append(
                        f"{label} remains at registered recovery path {active}: "
                        f"{_error_description(recovery_cleanup_error)}"
                    )
                    continue
                continue
            errors.append(
                f"{label} remains at {active}: {_error_description(cleanup_error)}"
            )

    remaining = [candidate for candidate in (path, quarantine) if candidate is not None and exists(candidate)]
    for candidate in remaining:
        if not any(str(candidate) in error for error in errors):
            errors.append(f"{label} remains at registered recovery path {candidate}")
    if cancellation is not None and not errors and not defer_cancellation:
        raise cancellation
    return errors


@PROJECT_FORMAT_CHECKER.checks("date-time")
def is_datetime(value: object) -> bool:
    if not isinstance(value, str):
        return True
    if not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})",
        value,
    ):
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        return False
    return parsed.tzinfo is not None


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


def json_sha256(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@contextmanager
def file_writer_lock(lock_path: Path) -> Iterator[None]:
    """Hold one re-entrant, cross-process exclusive lock for a filesystem scope."""

    normalized = lock_path.expanduser().absolute()
    held = getattr(_LOCK_STATE, "held", None)
    if held is None:
        held = {}
        _LOCK_STATE.held = held
    key = str(normalized)
    existing = held.get(key)
    if existing is not None:
        valid = False
        try:
            descriptor = existing.get("descriptor")
            descriptor_stat = os.fstat(descriptor)
            path_stat = os.stat(normalized, follow_symlinks=False)
            valid = bool(
                existing.get("valid") is True
                and isinstance(existing.get("depth"), int)
                and existing["depth"] >= 1
                and stat.S_ISREG(descriptor_stat.st_mode)
                and stat.S_ISREG(path_stat.st_mode)
                and existing.get("identity") == (descriptor_stat.st_dev, descriptor_stat.st_ino)
                and (descriptor_stat.st_dev, descriptor_stat.st_ino) == (path_stat.st_dev, path_stat.st_ino)
            )
        except (OSError, TypeError, ValueError):
            valid = False
        if valid:
            previous_depth = existing["depth"]
            body_error: BaseException | None = None
            try:
                existing["depth"] = previous_depth + 1
                yield
            except BaseException as error:
                body_error = error
            finally:
                if held.get(key) is existing:
                    existing["depth"] = previous_depth
            if body_error is not None:
                raise body_error
            return

        held.pop(key, None)
        existing["valid"] = False
        stale_errors: list[str] = []
        stale_descriptor = existing.get("descriptor")
        if isinstance(stale_descriptor, int):
            stale_owned = False
            try:
                stale_stat = os.fstat(stale_descriptor)
                current_path_stat = os.stat(normalized, follow_symlinks=False)
                stale_identity = (stale_stat.st_dev, stale_stat.st_ino)
                stale_owned = stale_identity == existing.get("identity") or stale_identity == (
                    current_path_stat.st_dev,
                    current_path_stat.st_ino,
                )
            except OSError:
                pass
            if stale_owned:
                try:
                    fcntl.flock(stale_descriptor, fcntl.LOCK_UN)
                except BaseException as unlock_error:
                    stale_errors.append(f"stale lock unlock failed: {_error_description(unlock_error)}")
                try:
                    os.close(stale_descriptor)
                except BaseException as close_error:
                    stale_errors.append(f"stale lock descriptor close failed: {_error_description(close_error)}")
        if stale_errors:
            raise TransactionRecoveryError(
                f"stale writer-lock state for {normalized} could not be reconciled: {'; '.join(stale_errors)}"
            )

    normalized.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    entry: dict[str, Any] | None = None
    body_error: BaseException | None = None
    cleanup_errors: list[str] = []
    try:
        transaction_trace("lock.before-open", path=str(normalized))
        descriptor = os.open(normalized, flags, 0o600)
        transaction_trace("lock.after-open", path=str(normalized), descriptor=descriptor)
        descriptor_stat = os.fstat(descriptor)
        if not stat.S_ISREG(descriptor_stat.st_mode):
            raise ValueError(f"writer lock is not a regular file: {normalized}")
        transaction_trace("lock.before-flock", path=str(normalized), descriptor=descriptor)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        transaction_trace("lock.after-flock", path=str(normalized), descriptor=descriptor)
        entry = {
            "depth": 1,
            "descriptor": descriptor,
            "identity": (descriptor_stat.st_dev, descriptor_stat.st_ino),
            "valid": True,
        }
        transaction_trace("lock.before-state-publication", path=str(normalized), descriptor=descriptor)
        held[key] = entry
        transaction_trace("lock.after-state-publication", path=str(normalized), descriptor=descriptor)
        try:
            yield
        except BaseException as error:
            body_error = error
    except OSError as error:
        if descriptor is None:
            body_error = ValueError(f"cannot safely open writer lock: {normalized}")
            body_error.__cause__ = error
        else:
            body_error = error
    except BaseException as error:
        body_error = error

    try:
        try:
            transaction_trace("lock.before-state-removal", path=str(normalized), descriptor=descriptor)
            if entry is not None:
                entry["valid"] = False
                if held.get(key) is entry:
                    held.pop(key, None)
            if not held:
                try:
                    delattr(_LOCK_STATE, "held")
                except AttributeError:
                    pass
            transaction_trace("lock.after-state-removal", path=str(normalized), descriptor=descriptor)
        except BaseException as state_error:
            if body_error is None:
                body_error = state_error
        finally:
            if entry is not None:
                entry["valid"] = False
                if held.get(key) is entry:
                    held.pop(key, None)
            if not held:
                try:
                    delattr(_LOCK_STATE, "held")
                except AttributeError:
                    pass
    finally:
        if descriptor is not None:
            try:
                try:
                    transaction_trace("lock.before-unlock", path=str(normalized), descriptor=descriptor)
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                    transaction_trace("lock.after-unlock", path=str(normalized), descriptor=descriptor)
                except BaseException as unlock_error:
                    if isinstance(unlock_error, Exception):
                        cleanup_errors.append(f"writer-lock unlock failed: {_error_description(unlock_error)}")
                    elif body_error is None:
                        body_error = unlock_error
            finally:
                try:
                    try:
                        transaction_trace("lock.before-close", path=str(normalized), descriptor=descriptor)
                    except BaseException as trace_error:
                        if body_error is None:
                            body_error = trace_error
                finally:
                    try:
                        os.close(descriptor)
                    except BaseException as close_error:
                        descriptor_still_open = True
                        try:
                            os.fstat(descriptor)
                        except OSError:
                            descriptor_still_open = False
                        if descriptor_still_open:
                            cleanup_errors.append(
                                f"writer-lock descriptor close failed: {_error_description(close_error)}"
                            )
                        elif not isinstance(close_error, Exception) and body_error is None:
                            body_error = close_error
                    try:
                        transaction_trace("lock.after-close", path=str(normalized), descriptor=descriptor)
                    except BaseException as trace_error:
                        if body_error is None:
                            body_error = trace_error

    if cleanup_errors:
        description = _error_description(body_error) if body_error is not None else "no prior operation error"
        raise TransactionRecoveryError(
            f"writer-lock operation failed ({description}); lock recovery was incomplete for {normalized}: "
            f"{'; '.join(cleanup_errors)}"
        ) from body_error
    if body_error is not None:
        raise body_error


@contextmanager
def project_writer_lock(project_dir: Path) -> Iterator[None]:
    project_root = project_dir.expanduser().resolve()
    with file_writer_lock(project_root / ".petkit-writer.lock"):
        yield


def safe_project_directory(project_dir: Path, relative: str | Path, *, create: bool = False) -> Path:
    """Resolve a project-internal directory while rejecting symbolic-link components."""

    project_root = project_dir.expanduser().resolve()
    relative_path = Path(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ValueError(f"project-internal path must be relative: {relative}")
    current = project_root
    for part in relative_path.parts:
        current = current / part
        if os.path.lexists(current):
            if current.is_symlink():
                raise ValueError(f"project-internal directory must not be symbolic: {current}")
            if not current.is_dir():
                raise ValueError(f"project-internal directory is not a directory: {current}")
            resolved = current.resolve()
            if not resolved.is_relative_to(project_root):
                raise ValueError(f"project-internal directory escapes the project: {current}")
        elif create:
            current.mkdir()
    return current


def ensure_tree_has_no_symlinks(root: Path, *, boundary: Path | None = None) -> None:
    if root.is_symlink():
        raise ValueError(f"directory must not be symbolic: {root}")
    if not root.is_dir():
        raise ValueError(f"required directory is missing: {root}")
    resolved_root = root.resolve()
    if boundary is not None and not resolved_root.is_relative_to(boundary.resolve()):
        raise ValueError(f"directory escapes its allowed boundary: {root}")
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"directory tree must not contain symbolic links: {path}")
        if boundary is not None and not path.resolve().is_relative_to(boundary.resolve()):
            raise ValueError(f"directory entry escapes its allowed boundary: {path}")


def source_image_files(directory: Path, *, boundary: Path | None = None) -> list[Path]:
    if directory.is_symlink():
        raise ValueError(f"source frame directory must not be symbolic: {directory}")
    if not directory.is_dir():
        return []
    resolved = directory.resolve()
    if boundary is not None and not resolved.is_relative_to(boundary.resolve()):
        raise ValueError(f"source frame directory escapes the project: {directory}")
    files: list[Path] = []
    for path in sorted(directory.iterdir()):
        if path.is_symlink():
            raise ValueError(f"source frame must not be symbolic: {path}")
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
            if boundary is not None and not path.resolve().is_relative_to(boundary.resolve()):
                raise ValueError(f"source frame escapes the project: {path}")
            files.append(path)
    return files


def copy_tree_without_symlinks(source: Path, destination: Path, *, boundary: Path) -> None:
    ensure_tree_has_no_symlinks(source, boundary=boundary)
    destination.mkdir(parents=True, exist_ok=False)
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        target = destination / relative
        if path.is_dir():
            target.mkdir()
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        else:
            raise ValueError(f"unsupported project tree entry: {path}")


def fork_build_parameters(project: dict[str, Any]) -> dict[str, Any]:
    generation = project.get("generation")
    if not isinstance(generation, dict):
        return {"chroma_key": None, "chroma_threshold": None}
    threshold = generation.get("chroma_threshold")
    return {
        "chroma_key": generation.get("chroma_key"),
        "chroma_threshold": float(threshold) if isinstance(threshold, (int, float)) else None,
    }


def source_file_snapshot(project_dir: Path) -> dict[str, str]:
    """Hash every retained source file without following symbolic links."""

    source_root = project_dir / "source"
    snapshot: dict[str, str] = {}
    if source_root.is_symlink():
        raise ValueError(f"project source must not be symbolic: {source_root}")
    if not source_root.is_dir():
        return snapshot
    ensure_tree_has_no_symlinks(source_root, boundary=project_dir)
    for path in sorted(source_root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"project source must not contain symbolic links: {path}")
        if path.is_file():
            snapshot[path.relative_to(source_root).as_posix()] = sha256_file(path)
    return snapshot


def recorded_authority_values(project: dict[str, Any]) -> dict[str, Any]:
    """Return the metadata values that a build later verifies against their files."""

    identity = project.get("identity")
    generation = project.get("generation")
    look = project.get("look")
    rows = generation.get("row_sources") if isinstance(generation, dict) else None
    mechanics = look.get("mechanics") if isinstance(look, dict) else None
    cardinals = look.get("cardinals") if isinstance(look, dict) else None
    approval = look.get("row_9_approval") if isinstance(look, dict) else None
    look_a = rows.get("look-a") if isinstance(rows, dict) else None
    look_b = rows.get("look-b") if isinstance(rows, dict) else None
    return {
        "canonical_identity_sha256": identity.get("canonical_sha256") if isinstance(identity, dict) else None,
        "mechanics_sha256": mechanics.get("sha256") if isinstance(mechanics, dict) else None,
        "cardinals_sha256": cardinals.get("sha256") if isinstance(cardinals, dict) else None,
        "look_a_sha256": look_a.get("sha256") if isinstance(look_a, dict) else None,
        "look_b_sha256": look_b.get("sha256") if isinstance(look_b, dict) else None,
        "row_9_basis_sha256": approval.get("basis_sha256") if isinstance(approval, dict) else None,
        "look_b_row_9_basis_sha256": look_b.get("row_9_basis_sha256") if isinstance(look_b, dict) else None,
    }


def _is_same_or_below(path: Path, ancestor: Path) -> bool:
    if path == ancestor or path.is_relative_to(ancestor):
        return True
    if not os.path.lexists(ancestor):
        return False
    for candidate in (path, *path.parents):
        if not os.path.lexists(candidate):
            continue
        try:
            if candidate.samefile(ancestor):
                return True
        except OSError as exc:
            raise ValueError(f"cannot safely resolve path identity for {candidate}") from exc
    return False


def paths_overlap(left: Path, right: Path) -> bool:
    """Return whether two resolved paths are equal or contain one another physically."""

    return _is_same_or_below(left, right) or _is_same_or_below(right, left)


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


def atomic_write_json(path: Path, value: Any, *, operation_id: str | None = None) -> None:
    """Atomically replace JSON through a deterministic, recoverable temporary path."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, sort_keys=False) + "\n"
    write_id = operation_id or uuid.uuid4().hex
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", write_id):
        raise ValueError(f"invalid atomic-write operation id: {write_id!r}")
    temporary_path = path.parent / f".{path.name}.write-{write_id}.tmp"
    recovery_path = path.parent / f".{path.name}.write-{write_id}.recovery"
    initial_cleanup = recover_operation_path(
        temporary_path,
        f"stale atomic JSON temporary file for {path}",
        quarantine=recovery_path,
    )
    if initial_cleanup:
        raise TransactionRecoveryError("; ".join(initial_cleanup))
    descriptor: int | None = None
    try:
        transaction_trace("json.before-temp-create", path=str(path), temporary=str(temporary_path))
        descriptor = os.open(
            temporary_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        transaction_trace("json.after-temp-create", path=str(path), temporary=str(temporary_path))
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = None
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        transaction_trace("json.after-temp-write", path=str(path), temporary=str(temporary_path))
        transaction_trace("json.before-replace", path=str(path), temporary=str(temporary_path))
        os.replace(temporary_path, path)
        transaction_trace("json.after-replace", path=str(path), temporary=str(temporary_path))
    except BaseException as operation_error:
        close_errors: list[str] = []
        if descriptor is not None:
            try:
                os.close(descriptor)
            except BaseException as close_error:
                close_errors.append(
                    f"atomic JSON descriptor for {temporary_path} could not be closed: "
                    f"{_error_description(close_error)}"
                )
        cleanup_errors = recover_operation_path(
            temporary_path,
            f"atomic JSON temporary file for {path}",
            quarantine=recovery_path,
            defer_cancellation=True,
        )
        recovery_errors = [*close_errors, *cleanup_errors]
        if recovery_errors:
            raise TransactionRecoveryError(
                f"atomic JSON write failed ({_error_description(operation_error)}); "
                f"recovery was incomplete: {'; '.join(recovery_errors)}"
            ) from operation_error
        raise


def operation_marker_path(project_dir: Path, operation_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", operation_id):
        raise ValueError(f"invalid transaction operation id: {operation_id!r}")
    return project_dir / RECOVERY_DIR / f"{operation_id}.json"


def write_operation_marker(project_dir: Path, marker: dict[str, Any]) -> Path:
    operation_id = marker.get("operation_id")
    if not isinstance(operation_id, str):
        raise ValueError("transaction marker requires an operation_id")
    recovery_root = safe_project_directory(project_dir, RECOVERY_DIR, create=True)
    marker_path = operation_marker_path(project_dir, operation_id)
    atomic_write_json(marker_path, marker, operation_id=f"marker-{operation_id}")
    return marker_path


def remove_operation_marker(marker_path: Path, *, defer_cancellation: bool = False) -> list[str]:
    recovery_path = marker_path.with_name(f".{marker_path.name}.cleanup")
    return recover_operation_path(
        marker_path,
        "transaction recovery marker",
        quarantine=recovery_path,
        defer_cancellation=defer_cancellation,
    )


def append_event(project_dir: Path, event: str, details: dict[str, Any]) -> None:
    with project_writer_lock(project_dir):
        history = safe_project_directory(project_dir, "history", create=True)
        path = history / "events.jsonl"
        if path.is_symlink():
            raise ValueError(f"project history must not be symbolic: {path}")
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


def reconcile_pending_operations(project_dir: Path) -> None:
    """Reconcile durable transaction markers before starting another mutation."""

    recovery_root = project_dir / RECOVERY_DIR
    if not os.path.lexists(recovery_root):
        return
    if recovery_root.is_symlink() or not recovery_root.is_dir():
        raise TransactionRecoveryError(f"transaction recovery root is unsafe: {recovery_root}")

    errors: list[str] = []
    for temporary in sorted(recovery_root.glob(".*.write-*.tmp")) + sorted(
        recovery_root.glob(".*.write-*.recovery")
    ):
        errors.extend(
            recover_operation_path(
                temporary,
                "incomplete transaction-marker write",
                defer_cancellation=True,
            )
        )
    marker_candidates = sorted(recovery_root.glob("*.json")) + sorted(
        recovery_root.glob(".*.json.cleanup")
    )
    seen_operations: set[str] = set()
    for marker_path in marker_candidates:
        if not os.path.lexists(marker_path):
            continue
        try:
            marker = read_json(marker_path)
            operation_id = marker.get("operation_id")
            canonical_marker = operation_marker_path(project_dir, operation_id)
            marker_recovery = canonical_marker.with_name(f".{canonical_marker.name}.cleanup")
            if marker_path not in {canonical_marker, marker_recovery}:
                raise ValueError("marker path does not match its operation id")
            if operation_id in seen_operations:
                continue
            seen_operations.add(operation_id)
            kind = marker.get("kind")
            if kind == "identity":
                marker_errors = _reconcile_identity_marker(project_dir, marker)
            elif kind == "variant":
                marker_errors = _reconcile_variant_marker(project_dir, marker)
            elif kind == "build":
                from petkit.build import reconcile_build_marker

                marker_errors = reconcile_build_marker(project_dir, marker)
            else:
                raise ValueError(f"unknown transaction kind: {kind!r}")
        except BaseException as recovery_error:
            if isinstance(recovery_error, TransactionRecoveryError):
                errors.append(str(recovery_error))
            else:
                errors.append(
                    f"transaction marker {marker_path} could not be reconciled: "
                    f"{_error_description(recovery_error)}"
                )
            continue
        errors.extend(marker_errors)
        if not marker_errors:
            errors.extend(remove_operation_marker(canonical_marker, defer_cancellation=True))
    if errors:
        raise TransactionRecoveryError(
            f"pending project transaction recovery was incomplete: {'; '.join(errors)}"
        )


def project_mutation(function: Any) -> Any:
    """Run an existing-project mutation under the shared writer lock."""

    @wraps(function)
    def wrapped(project_value: str | Path, *args: Any, **kwargs: Any) -> Any:
        path = project_path(project_value)
        with project_writer_lock(path):
            reconcile_pending_operations(path)
            return function(path, *args, **kwargs)

    return wrapped


@lru_cache(maxsize=1)
def project_validator() -> Draft202012Validator:
    schema = read_json(PROJECT_SCHEMA_FILE)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=PROJECT_FORMAT_CHECKER)


def validate_project(project: dict[str, Any]) -> None:
    errors = sorted(
        project_validator().iter_errors(project),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        if location == "contract_version":
            raise ValueError(
                "this workshop supports only pet contract version 2; "
                "run upgrade-project for an older local project"
            )
        raise ValueError(f"invalid project metadata at {location}: {error.message}")
    load_contract(int(project["contract_version"]))


def save_project(
    project_dir: Path,
    project: dict[str, Any],
    *,
    expected_current_sha256: str | None = None,
    operation_id: str | None = None,
    reconcile_pending: bool = True,
) -> None:
    with project_writer_lock(project_dir):
        if reconcile_pending:
            reconcile_pending_operations(project_dir)
        if expected_current_sha256 is not None:
            current = read_json(project_dir / PROJECT_FILE)
            if json_sha256(current) != expected_current_sha256:
                raise ValueError("project metadata changed during the operation; refusing to overwrite concurrent work")
        project["updated_at"] = now_iso()
        validate_project(project)
        atomic_write_json(project_dir / PROJECT_FILE, project, operation_id=operation_id)


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
    if os.path.lexists(destination):
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
    root.mkdir(parents=True, exist_ok=True)
    staging = root / f".{normalized_id}.init-{uuid.uuid4().hex}"
    try:
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
            (staging / relative).mkdir(parents=True, exist_ok=True)

        copied_references: list[str] = []
        for index, source in enumerate(resolved_references, start=1):
            suffix = source.suffix.lower() or ".png"
            target = staging / "references" / "original" / f"reference-{index:02d}{suffix}"
            shutil.copy2(source, target)
            copied_references.append(target.relative_to(staging).as_posix())

        created_at = now_iso()
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
        with project_writer_lock(staging):
            save_project(staging, project)
            (staging / IDENTITY_FILE).write_text(
                "# Identity and art direction\n\n"
                f"## Concept\n\n{concept.strip() or 'To be established.'}\n\n"
                f"## Style\n\n{style.strip() or 'Auto; infer from the approved identity reference.'}\n\n"
                "## Invariants\n\n"
                "Record the silhouette, proportions, face, palette, materials, markings, props, and avoidances that every state must preserve.\n",
                encoding="utf-8",
            )
            append_event(staging, "project-created", {"id": normalized_id})
        try:
            os.rename(staging, destination)
        except OSError as exc:
            if os.path.lexists(destination):
                raise ValueError(f"pet project already exists: {destination}") from exc
            raise
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return destination


def restore_project_snapshot_for_recovery(
    project_dir: Path,
    snapshot: dict[str, Any],
    label: str,
    *,
    operation_id: str | None = None,
) -> list[str]:
    path = project_dir / PROJECT_FILE
    try:
        atomic_write_json(path, snapshot, operation_id=operation_id)
    except BaseException as recovery_error:
        try:
            if read_json(path) == snapshot:
                return []
        except (OSError, ValueError):
            pass
        return [f"{label} could not be restored at {path}: {_error_description(recovery_error)}"]
    return []


def _reconcile_identity_abnormal_exit(
    project_dir: Path,
    original_project: dict[str, Any],
    *,
    target: Path | None,
    target_staging: Path | None,
    target_staging_recovery: Path | None,
    displaced_target: Path | None,
    snapshot_root: Path | None,
    snapshot_recovery: Path | None,
    snapshot_hash: str | None,
    relative: str | None,
    operation_id: str,
) -> tuple[bool, list[str]]:
    """Make canonical bytes and durable identity metadata agree after cancellation."""

    errors: list[str] = []
    durable_project: dict[str, Any] | None = None
    try:
        durable_project = read_json(project_dir / PROJECT_FILE)
        validate_project(durable_project)
    except BaseException:
        metadata_errors = restore_project_snapshot_for_recovery(
            project_dir,
            original_project,
            "project metadata after interrupted identity approval",
            operation_id=f"identity-recovery-{operation_id}",
        )
        errors.extend(metadata_errors)
        if not metadata_errors:
            durable_project = original_project

    target_matches_new = False
    if target is not None and snapshot_hash is not None and os.path.lexists(target):
        try:
            target_matches_new = (
                not target.is_symlink()
                and target.is_file()
                and sha256_file(target) == snapshot_hash
            )
        except BaseException:
            target_matches_new = False
    durable_identity = durable_project.get("identity", {}) if durable_project is not None else {}
    metadata_matches_new = bool(
        relative is not None
        and snapshot_hash is not None
        and durable_identity.get("approved") is True
        and durable_identity.get("canonical_reference") == relative
        and durable_identity.get("canonical_sha256") == snapshot_hash
    )
    durable_commit = metadata_matches_new and target_matches_new

    preserve_displaced = False
    if not durable_commit:
        if displaced_target is not None and os.path.lexists(displaced_target) and target is not None:
            try:
                transaction_trace(
                    "identity.before-restore",
                    source=str(displaced_target),
                    target=str(target),
                )
                os.replace(displaced_target, target)
                transaction_trace(
                    "identity.after-restore",
                    source=str(displaced_target),
                    target=str(target),
                )
            except BaseException as recovery_error:
                restored = False
                old_hash = original_project.get("identity", {}).get("canonical_sha256")
                if isinstance(old_hash, str) and os.path.lexists(target):
                    try:
                        restored = sha256_file(target) == old_hash and not os.path.lexists(displaced_target)
                    except BaseException:
                        restored = False
                if not restored:
                    preserve_displaced = True
                    errors.append(
                        f"the only verified previous canonical identity remains at stable recovery path "
                        f"{displaced_target} and could not be restored "
                        f"to {target}: {_error_description(recovery_error)}"
                    )
        elif target is not None and os.path.lexists(target):
            old_relative = original_project.get("identity", {}).get("canonical_reference")
            old_target = project_dir / old_relative if isinstance(old_relative, str) else None
            if old_target is None or target != old_target:
                errors.extend(
                    recover_operation_path(
                        target,
                        "uncommitted canonical identity",
                        quarantine=target.parent / f".{target.name}.cancelled-{operation_id}",
                        defer_cancellation=True,
                    )
                )

        if durable_project != original_project:
            errors.extend(
                restore_project_snapshot_for_recovery(
                    project_dir,
                    original_project,
                    "identity metadata after interrupted approval",
                    operation_id=f"identity-recovery-{operation_id}",
                )
            )

        original_identity = original_project.get("identity", {})
        old_relative = original_identity.get("canonical_reference")
        old_hash = original_identity.get("canonical_sha256")
        if original_identity.get("approved") is True and isinstance(old_relative, str) and isinstance(old_hash, str):
            old_target = project_dir / old_relative
            try:
                if old_target.is_symlink() or not old_target.is_file() or sha256_file(old_target) != old_hash:
                    errors.append(
                        f"restored identity metadata expects {old_hash} at {old_target}, but those bytes are unavailable"
                    )
            except BaseException as recovery_error:
                errors.append(
                    f"restored canonical identity could not be verified at {old_target}: "
                    f"{_error_description(recovery_error)}"
                )

    if displaced_target is not None and os.path.lexists(displaced_target) and not preserve_displaced:
        errors.extend(
            recover_operation_path(
                displaced_target,
                "displaced canonical identity",
                quarantine=displaced_target.parent / f".{displaced_target.name}.cleanup-failed-{operation_id}",
                defer_cancellation=True,
            )
        )
    if target_staging is not None:
        errors.extend(
            recover_operation_path(
                target_staging,
                "canonical identity staging file",
                quarantine=target_staging_recovery,
                defer_cancellation=True,
            )
        )
    if snapshot_root is not None:
        errors.extend(
            recover_operation_path(
                snapshot_root,
                "private identity input snapshot",
                quarantine=snapshot_recovery,
                defer_cancellation=True,
            )
        )
    metadata_path = project_dir / PROJECT_FILE
    metadata_temporary = metadata_path.parent / f".{metadata_path.name}.write-identity-{operation_id}.tmp"
    metadata_recovery = metadata_path.parent / f".{metadata_path.name}.write-identity-{operation_id}.recovery"
    errors.extend(
        recover_operation_path(
            metadata_temporary,
            "identity metadata atomic-write temporary file",
            quarantine=metadata_recovery,
            defer_cancellation=True,
        )
    )
    return durable_commit, errors


def _reconcile_identity_marker(project_dir: Path, marker: dict[str, Any]) -> list[str]:
    operation_id = marker.get("operation_id")
    original_project = marker.get("original_project")
    if not isinstance(operation_id, str) or not isinstance(original_project, dict):
        raise ValueError("identity recovery marker is incomplete")
    approved_root = safe_project_directory(project_dir, "references/approved")
    target = Path(str(marker.get("target")))
    if target.parent != approved_root or not target.name.startswith("canonical-base"):
        raise ValueError(f"identity recovery target is outside its approved root: {target}")
    target_staging = approved_root / f".{target.name}.staging-{operation_id}"
    target_staging_recovery = approved_root / f".{target_staging.name}.cleanup"
    displaced_target = approved_root / f".{target.name}.preapproval-{operation_id}"
    snapshot_root = Path(tempfile.gettempdir()) / f"petkit-identity-{operation_id}-inputs"
    snapshot_recovery = snapshot_root.parent / f".{snapshot_root.name}.recovery"
    _durable_commit, errors = _reconcile_identity_abnormal_exit(
        project_dir,
        original_project,
        target=target,
        target_staging=target_staging,
        target_staging_recovery=target_staging_recovery,
        displaced_target=displaced_target,
        snapshot_root=snapshot_root,
        snapshot_recovery=snapshot_recovery,
        snapshot_hash=marker.get("snapshot_sha256") if isinstance(marker.get("snapshot_sha256"), str) else None,
        relative=marker.get("relative") if isinstance(marker.get("relative"), str) else None,
        operation_id=operation_id,
    )
    return errors


@project_mutation
def approve_identity(project_value: str | Path, canonical_reference: Path) -> dict[str, Any]:
    project_dir, project = load_project(project_value)
    original_project = json.loads(json.dumps(project))
    requested = canonical_reference.expanduser().absolute()
    if requested.is_symlink():
        raise ValueError(f"canonical identity image must not be symbolic: {requested}")
    operation_id = uuid.uuid4().hex
    suffix = requested.suffix.lower() or ".png"
    approved_root = safe_project_directory(project_dir, "references/approved")
    target = approved_root / f"canonical-base{suffix}"
    target_staging = approved_root / f".{target.name}.staging-{operation_id}"
    target_staging_recovery = approved_root / f".{target_staging.name}.cleanup"
    displaced_target = approved_root / f".{target.name}.preapproval-{operation_id}"
    snapshot_root = Path(tempfile.gettempdir()) / f"petkit-identity-{operation_id}-inputs"
    snapshot_recovery = snapshot_root.parent / f".{snapshot_root.name}.recovery"
    snapshot = snapshot_root / f"identity{requested.suffix.lower() or '.png'}"
    snapshot_hash: str | None = None
    relative = target.relative_to(project_dir).as_posix()
    marker = {
        "schema_version": 1,
        "kind": "identity",
        "operation_id": operation_id,
        "original_project": original_project,
        "target": str(target),
        "relative": relative,
        "snapshot_sha256": None,
    }
    marker_path = operation_marker_path(project_dir, operation_id)
    try:
        marker_path = write_operation_marker(project_dir, marker)
        transaction_trace("identity.before-snapshot-mkdir", path=str(snapshot_root))
        snapshot_root.mkdir(mode=0o700)
        transaction_trace("identity.after-snapshot-mkdir", path=str(snapshot_root))
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(requested, flags)
        except OSError as exc:
            raise ValueError(f"canonical identity image does not exist or is unsafe: {requested}") from exc
        try:
            source_stat = os.fstat(descriptor)
            if not stat.S_ISREG(source_stat.st_mode):
                raise ValueError(f"canonical identity image must be a regular file: {requested}")
            with os.fdopen(descriptor, "rb", closefd=False) as source_handle, snapshot.open("xb") as target_handle:
                shutil.copyfileobj(source_handle, target_handle)
                target_handle.flush()
                os.fsync(target_handle.fileno())
        finally:
            os.close(descriptor)
        snapshot_hash = sha256_file(snapshot)
        marker["snapshot_sha256"] = snapshot_hash
        write_operation_marker(project_dir, marker)
        if target.is_symlink():
            raise ValueError(f"canonical identity target must not be symbolic: {target}")
        shutil.copy2(snapshot, target_staging)
        if target_staging.is_symlink() or sha256_file(target_staging) != snapshot_hash:
            raise ValueError("canonical identity changed while preparing its project-local copy")
        snapshot_cleanup_errors = recover_operation_path(
            snapshot_root,
            "private identity input snapshot",
            quarantine=snapshot_recovery,
        )
        if snapshot_cleanup_errors:
            raise TransactionRecoveryError("; ".join(snapshot_cleanup_errors))

        if os.path.lexists(target):
            if target.is_symlink() or not target.is_file():
                raise ValueError(f"canonical identity target must be a regular file: {target}")
            os.replace(target, displaced_target)
        os.replace(target_staging, target)
        target_hash = sha256_file(target)
        if target_hash != snapshot_hash:
            raise ValueError("canonical identity target no longer matches its verified private snapshot")

        identity = project.get("identity", {})
        previously_approved = identity.get("approved") is True
        previous_hash = identity.get("canonical_sha256")
        identity_changed = previously_approved and previous_hash != target_hash
        relative = target.relative_to(project_dir).as_posix()
        approved_at = now_iso()
        project["identity"].update(
            {
                "approved": True,
                "approved_at": approved_at,
                "canonical_reference": relative,
                "canonical_sha256": target_hash,
            }
        )
        if identity_changed:
            project["look"]["cardinals"] = None
            project["look"]["row_9_approved"] = False
            project["look"]["row_9_approval"] = None
            accepted_build = project.get("accepted_build")
            if isinstance(accepted_build, str):
                project.setdefault("generation", {})["pre_identity_accepted_build"] = accepted_build
            project["accepted_build"] = None
            project["active_edit"] = None
        if not previously_approved or identity_changed:
            project["status"] = "identity-approved"
        save_project(
            project_dir,
            project,
            operation_id=f"identity-{operation_id}",
            reconcile_pending=False,
        )

        post_commit_warnings: list[str] = []
        if displaced_target is not None:
            try:
                displaced_target.unlink(missing_ok=True)
            except OSError as cleanup_error:
                post_commit_warnings.append(
                    f"identity and metadata were committed, but the displaced prior canonical image remains at "
                    f"{displaced_target}: {cleanup_error}"
                )
        try:
            append_event(project_dir, "identity-approved", {"reference": relative})
        except (OSError, ValueError) as event_error:
            post_commit_warnings.append(
                f"identity and metadata were committed, but the identity-approved event could not be recorded: "
                f"{event_error}"
            )
        result = dict(project)
        result["post_commit_warnings"] = post_commit_warnings
        marker_cleanup_errors = remove_operation_marker(marker_path)
        if marker_cleanup_errors:
            raise TransactionRecoveryError("; ".join(marker_cleanup_errors))
        return result
    except BaseException as operation_error:
        durable_commit, recovery_errors = _reconcile_identity_abnormal_exit(
            project_dir,
            original_project,
            target=target,
            target_staging=target_staging,
            target_staging_recovery=target_staging_recovery,
            displaced_target=displaced_target,
            snapshot_root=snapshot_root,
            snapshot_recovery=snapshot_recovery,
            snapshot_hash=snapshot_hash,
            relative=relative,
            operation_id=operation_id,
        )
        if not recovery_errors:
            recovery_errors.extend(remove_operation_marker(marker_path, defer_cancellation=True))
        if recovery_errors:
            raise TransactionRecoveryError(
                f"identity approval failed ({_error_description(operation_error)}); "
                f"transaction recovery was incomplete: {'; '.join(recovery_errors)}"
            ) from operation_error
        if durable_commit:
            operation_error.add_note(
                f"canonical identity {snapshot_hash} and its metadata were durably committed before "
                f"cancellation; the committed identity was preserved"
            )
        raise


def _require_variant_parent_baseline(source_dir: Path, source: dict[str, Any]) -> str:
    accepted_build = source.get("accepted_build")
    if (
        source.get("identity", {}).get("approved") is not True
        or not isinstance(accepted_build, str)
        or source.get("current_build") != accepted_build
        or source.get("status") != "accepted"
        or source.get("active_edit") is not None
    ):
        raise ValueError("variant creation requires an accepted, current parent baseline with no active edit")
    return accepted_build


def next_build_id(project_dir: Path) -> str:
    numbers: set[int] = set()

    def record(value: object) -> None:
        if not isinstance(value, str):
            return
        match = re.fullmatch(r"build-(\d{4})", value)
        if match:
            numbers.add(int(match.group(1)))

    builds_root = safe_project_directory(project_dir, "builds", create=True)
    for path in builds_root.glob("build-*"):
        record(path.name)
    history_root = safe_project_directory(project_dir, "history", create=True)
    for path in history_root.glob("candidate-build-*.json"):
        match = re.fullmatch(r"candidate-(build-\d{4})\.json", path.name)
        if match:
            record(match.group(1))
    for path in history_root.glob("acceptance-build-*.json"):
        match = re.fullmatch(r"acceptance-(build-\d{4})\.json", path.name)
        if match:
            record(match.group(1))
    events_path = history_root / "events.jsonl"
    if events_path.is_file() and not events_path.is_symlink():
        try:
            for line in events_path.read_text(encoding="utf-8").splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    record(event.get("build_id"))
        except OSError:
            pass
    recovery_root = project_dir / RECOVERY_DIR
    if recovery_root.is_dir() and not recovery_root.is_symlink():
        for marker_path in recovery_root.glob("*.json"):
            try:
                marker = read_json(marker_path)
            except (OSError, ValueError):
                continue
            if marker.get("kind") == "build":
                record(marker.get("build_id"))
    return f"build-{max(numbers, default=0) + 1:04d}"


def current_build_dir(project_dir: Path, project: dict[str, Any]) -> Path:
    build_id = project.get("current_build")
    if not build_id:
        raise ValueError(f"project has no current build: {project_dir}")
    if not isinstance(build_id, str) or not re.fullmatch(r"build-\d{4}", build_id):
        raise ValueError(f"project has an invalid current build id: {build_id!r}")
    builds_root = safe_project_directory(project_dir, "builds")
    path = builds_root / str(build_id)
    if path.is_symlink() or not path.is_dir() or not path.resolve().is_relative_to(builds_root.resolve()):
        raise ValueError(f"current build is missing: {path}")
    return path


def _reconcile_variant_abnormal_exit(
    source_dir: Path,
    *,
    operation_id: str,
    normalized_id: str,
    staging: Path,
    destination: Path,
    parent_snapshot: Path,
    parent_snapshot_recovery: Path,
    expected_project_hash: str | None,
) -> tuple[bool, list[str]]:
    errors: list[str] = []
    operation_marker = destination / ".petkit-variant-operation.json"
    marker_recovery = destination / f".petkit-variant-operation.recovery-{operation_id}"
    operation_owned = False
    destination_verified = False
    if os.path.lexists(destination):
        try:
            if operation_marker.is_file() and not operation_marker.is_symlink():
                payload = read_json(operation_marker)
                operation_owned = payload.get("operation_id") == operation_id
            if expected_project_hash is not None:
                destination_verified = (
                    not destination.is_symlink()
                    and destination.is_dir()
                    and sha256_file(destination / PROJECT_FILE) == expected_project_hash
                )
                operation_owned = operation_owned or destination_verified
        except (OSError, ValueError):
            destination_verified = False

    durable_commit = bool(destination_verified)
    if durable_commit:
        errors.extend(
            recover_operation_path(
                operation_marker,
                f"variant publication marker for {normalized_id}",
                quarantine=marker_recovery,
                defer_cancellation=True,
            )
        )
    elif os.path.lexists(destination):
        if operation_owned:
            errors.extend(
                recover_operation_path(
                    destination,
                    f"interrupted variant project {normalized_id}",
                    quarantine=destination.parent / f".{normalized_id}.cancelled-{operation_id}",
                    quarantine_first=True,
                    defer_cancellation=True,
                )
            )
        else:
            errors.append(
                f"variant destination at {destination} could not be proven to belong to operation {operation_id}"
            )
    errors.extend(
        recover_operation_path(
            staging,
            f"interrupted variant staging project {normalized_id}",
            quarantine=staging.parent / f".{staging.name}.cleanup",
            defer_cancellation=True,
        )
    )
    errors.extend(
        recover_operation_path(
            parent_snapshot,
            f"private variant parent snapshot for {normalized_id}",
            quarantine=parent_snapshot_recovery,
            defer_cancellation=True,
        )
    )
    return durable_commit, errors


def _reconcile_variant_marker(project_dir: Path, marker: dict[str, Any]) -> list[str]:
    operation_id = marker.get("operation_id")
    normalized_id = marker.get("variant_id")
    if not isinstance(operation_id, str) or not isinstance(normalized_id, str):
        raise ValueError("variant recovery marker is incomplete")
    root = Path(str(marker.get("root")))
    staging = root / f".{normalized_id}.variant-{operation_id}"
    destination = root / normalized_id
    accepted_build = marker.get("accepted_build")
    if not isinstance(accepted_build, str) or not re.fullmatch(r"build-\d{4}", accepted_build):
        raise ValueError("variant recovery marker lacks its accepted parent build")
    parent_snapshot_path = Path(tempfile.gettempdir()) / f"petkit-variant-{accepted_build}-{operation_id}-inputs"
    parent_snapshot_recovery = parent_snapshot_path.parent / f".{parent_snapshot_path.name}.recovery"
    if str(staging) != marker.get("staging") or str(destination) != marker.get("destination"):
        raise ValueError("variant recovery paths do not match their operation identity")
    if str(parent_snapshot_path) != marker.get("parent_snapshot"):
        raise ValueError("variant parent-snapshot recovery path does not match its operation identity")
    _durable_commit, errors = _reconcile_variant_abnormal_exit(
        project_dir,
        operation_id=operation_id,
        normalized_id=normalized_id,
        staging=staging,
        destination=destination,
        parent_snapshot=parent_snapshot_path,
        parent_snapshot_recovery=parent_snapshot_recovery,
        expected_project_hash=(
            marker.get("expected_project_sha256")
            if isinstance(marker.get("expected_project_sha256"), str)
            else None
        ),
    )
    return errors


@project_mutation
def create_variant(source_value: str | Path, root: Path, new_id: str, display_name: str) -> Path:
    source_dir, source = load_project(source_value)
    normalized_id = slugify(new_id)
    if normalized_id != new_id:
        raise ValueError(f"variant id must be the normalized slug {normalized_id!r}")
    if normalized_id == source["id"]:
        raise ValueError("variant id must be distinct from its parent id")
    root = root.expanduser().resolve()
    destination = root / normalized_id
    if paths_overlap(source_dir.resolve(), destination.resolve(strict=False)):
        raise ValueError("variant project and source project must not be equal or contain one another")
    if os.path.lexists(destination):
        raise ValueError(f"variant project already exists: {destination}")
    accepted_build = _require_variant_parent_baseline(source_dir, source)
    from petkit.build import verified_variant_parent_snapshot, verify_variant_parent_copy

    operation_id = uuid.uuid4().hex
    staging = root / f".{normalized_id}.variant-{operation_id}"
    operation_marker = ".petkit-variant-operation.json"
    expected_project_hash: str | None = None
    parent_snapshot_path = Path(tempfile.gettempdir()) / f"petkit-variant-{accepted_build}-{operation_id}-inputs"
    parent_snapshot_recovery = parent_snapshot_path.parent / f".{parent_snapshot_path.name}.recovery"
    marker = {
        "schema_version": 1,
        "kind": "variant",
        "operation_id": operation_id,
        "variant_id": normalized_id,
        "root": str(root),
        "staging": str(staging),
        "destination": str(destination),
        "accepted_build": accepted_build,
        "parent_snapshot": str(parent_snapshot_path),
        "expected_project_sha256": None,
    }
    marker_path = operation_marker_path(source_dir, operation_id)
    try:
        marker_path = write_operation_marker(source_dir, marker)
        with verified_variant_parent_snapshot(source_dir, source, accepted_build, operation_id=operation_id) as (
            verified_parent_snapshot,
            accepted_record,
        ):
            root.mkdir(parents=True, exist_ok=True)
            staging.mkdir(parents=False, exist_ok=False)
            atomic_write_json(
                staging / operation_marker,
                {"operation_id": operation_id, "variant_id": normalized_id, "parent_id": source["id"]},
            )
            copy_tree_without_symlinks(
                verified_parent_snapshot / "references",
                staging / "references",
                boundary=verified_parent_snapshot,
            )
            copy_tree_without_symlinks(
                verified_parent_snapshot / "source",
                staging / "source",
                boundary=verified_parent_snapshot,
            )
            for relative in ("builds", "history", "backups/installed", "qa"):
                (staging / relative).mkdir(parents=True, exist_ok=True)
            shutil.copy2(verified_parent_snapshot / IDENTITY_FILE, staging / IDENTITY_FILE)
            verify_variant_parent_copy(staging, source, accepted_record)
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
            variant.setdefault("generation", {})["fork_snapshot"] = {
                "schema_version": 2,
                "recorded_at": created_at,
                "parent_build": accepted_build,
                "accepted_source_sha256": accepted_record["source_sha256"],
                "accepted_build_inputs": accepted_record["build_inputs"],
                "source_sha256": source_file_snapshot(staging),
                "authority": recorded_authority_values(variant),
                "build_parameters": fork_build_parameters(variant),
            }
            save_project(staging, variant)
            append_event(staging, "variant-created", {"parent_id": source["id"], "parent_build": accepted_build})
            expected_project_hash = sha256_file(staging / PROJECT_FILE)
            marker["expected_project_sha256"] = expected_project_hash
            write_operation_marker(source_dir, marker)
        os.replace(staging, destination)
        transaction_trace("variant.after-publish", destination=str(destination), operation_id=operation_id)
        publication_marker = destination / operation_marker
        marker_cleanup_errors = recover_operation_path(
            publication_marker,
            f"variant publication marker for {normalized_id}",
            quarantine=destination / f".petkit-variant-operation.recovery-{operation_id}",
        )
        if marker_cleanup_errors:
            raise TransactionRecoveryError("; ".join(marker_cleanup_errors))
        transaction_trace("variant.after-publication-marker-removal", destination=str(destination))
        source_marker_errors = remove_operation_marker(marker_path)
        if source_marker_errors:
            raise TransactionRecoveryError("; ".join(source_marker_errors))
    except BaseException as operation_error:
        durable_commit, recovery_errors = _reconcile_variant_abnormal_exit(
            source_dir,
            operation_id=operation_id,
            normalized_id=normalized_id,
            staging=staging,
            destination=destination,
            parent_snapshot=parent_snapshot_path,
            parent_snapshot_recovery=parent_snapshot_recovery,
            expected_project_hash=expected_project_hash,
        )
        if not recovery_errors:
            recovery_errors.extend(remove_operation_marker(marker_path, defer_cancellation=True))
        if recovery_errors:
            raise TransactionRecoveryError(
                f"variant creation failed ({_error_description(operation_error)}); "
                f"transaction recovery was incomplete: {'; '.join(recovery_errors)}"
            ) from operation_error
        if durable_commit:
            operation_error.add_note(
                f"variant {normalized_id} was durably published at {destination} before cancellation; "
                "the verified child was preserved"
            )
        raise
    return destination


@project_mutation
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
    current_build = project.get("current_build")
    accepted_build = project.get("accepted_build")
    generation = project.get("generation")
    requires_accepted_local_baseline = bool(project.get("parent_id")) or (
        isinstance(generation, dict) and isinstance(generation.get("recovery_import"), dict)
    )
    if requires_accepted_local_baseline and not accepted_build:
        raise ValueError("imports and variants require an accepted child-local baseline before planning an edit")
    baseline = accepted_build or current_build
    if not baseline:
        raise ValueError("create a baseline build before planning an edit")
    comparison_build = current_build or baseline
    contract = load_contract(2)
    allowed = list(dict.fromkeys(allowed_states))
    for state_id in allowed:
        contract.state(state_id)
    baseline_record_path = project_dir / "builds" / str(baseline) / "build.json"
    if not baseline_record_path.is_file():
        raise ValueError(f"baseline build record is missing: {baseline_record_path}")
    try:
        baseline_record = json.loads(baseline_record_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"baseline build record is not valid JSON: {baseline_record_path}") from exc
    if not isinstance(baseline_record, dict):
        raise ValueError(f"baseline build record must be an object: {baseline_record_path}")
    if baseline_record.get("build_id") != baseline or baseline_record.get("pet_id") != project["id"]:
        raise ValueError("baseline build record does not match the selected project and build")
    baseline_inputs = baseline_record.get("build_inputs")
    if not isinstance(baseline_inputs, dict):
        raise ValueError("baseline build record is missing build inputs")
    baseline_authority = baseline_inputs.get("authority_fingerprint")
    if not isinstance(baseline_authority, str) or not re.fullmatch(r"[0-9a-f]{64}", baseline_authority):
        raise ValueError("baseline build predates integrity binding; run upgrade-project to establish a fresh baseline")
    edit_id = f"edit-{now_iso().replace(':', '').replace('-', '')}-{uuid.uuid4().hex[:8]}"
    record = {
        "schema_version": 1,
        "edit_id": edit_id,
        "planned_at": now_iso(),
        "mode": mode,
        "outcome": outcome.strip(),
        "initial_baseline_build": baseline,
        "comparison_build": comparison_build,
        "latest_build": None,
        "allowed_states": allowed,
        "invariants": [item.strip() for item in invariants if item.strip()],
        "baseline_source_sha256": baseline_record.get("source_sha256"),
        "baseline_build_inputs": baseline_inputs,
    }
    edit_scope_root = safe_project_directory(project_dir, "history/edit-scopes", create=True)
    relative = Path("history") / "edit-scopes" / f"{edit_id}.json"
    atomic_write_json(edit_scope_root / f"{edit_id}.json", record)
    project["active_edit"] = {**record, "record": relative.as_posix()}
    save_project(project_dir, project)
    append_event(
        project_dir,
        "edit-planned",
        {"edit_id": edit_id, "mode": mode, "baseline_build": baseline, "allowed_states": allowed},
    )
    return {"ok": True, "project": str(project_dir), **project["active_edit"]}


@project_mutation
def upgrade_project(project_value: str | Path) -> dict[str, Any]:
    """One-way metadata migration for projects created by this workshop before V2."""
    path = project_path(project_value)
    project = read_json(path / PROJECT_FILE)
    previous = project.get("contract_version")
    if previous == 2:
        validate_project(project)
        accepted_build = project.get("accepted_build")
        generation = project.setdefault("generation", {})
        fork_snapshot = generation.get("fork_snapshot")
        variant_rebaselined = bool(
            project.get("parent_id")
            and (
                not isinstance(fork_snapshot, dict)
                or fork_snapshot.get("schema_version") != 2
            )
        )
        recorded_at: str | None = None
        if variant_rebaselined:
            recorded_at = now_iso()
            generation["fork_snapshot"] = {
                "schema_version": 2,
                "recorded_at": recorded_at,
                "origin": "legacy-owner-rebaseline",
                "source_sha256": source_file_snapshot(path),
                "authority": recorded_authority_values(project),
                "build_parameters": fork_build_parameters(project),
            }
        if not accepted_build:
            if not variant_rebaselined:
                return {"ok": True, "project": str(path), "already_v2": True}
            save_project(path, project)
            append_event(
                path,
                "legacy-variant-rebaselined",
                {"parent_id": project["parent_id"], "recorded_at": recorded_at},
            )
            return {
                "ok": True,
                "project": str(path),
                "already_v2": True,
                "variant_integrity_rebaseline": True,
                "parent_id": project["parent_id"],
            }
        accepted_record_path = path / "builds" / str(accepted_build) / "build.json"
        try:
            accepted_record = read_json(accepted_record_path)
        except ValueError:
            accepted_record = {}
        accepted_inputs = accepted_record.get("build_inputs")
        authority_fingerprint = (
            accepted_inputs.get("authority_fingerprint")
            if isinstance(accepted_inputs, dict)
            else None
        )
        manifest_hash = accepted_record.get("pet_json_sha256")
        review_authority = accepted_record.get("review_authority_sha256")
        required_review_authority = {
            "qa-private/direction-blind-answer-key.json",
            "qa-private/semantic-recognition-answer-key.json",
            "qa/direction-continuity.json",
        }
        if (
            isinstance(authority_fingerprint, str)
            and re.fullmatch(r"[0-9a-f]{64}", authority_fingerprint)
            and isinstance(manifest_hash, str)
            and re.fullmatch(r"[0-9a-f]{64}", manifest_hash)
            and isinstance(review_authority, dict)
            and set(review_authority) == required_review_authority
            and all(
                isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
                for value in review_authority.values()
            )
        ):
            if variant_rebaselined:
                save_project(path, project)
                append_event(
                    path,
                    "legacy-variant-rebaselined",
                    {"parent_id": project["parent_id"], "recorded_at": recorded_at},
                )
            return {
                "ok": True,
                "project": str(path),
                "already_v2": True,
                "variant_integrity_rebaseline": variant_rebaselined,
            }
        project.setdefault("generation", {})["pre_integrity_accepted_build"] = accepted_build
        project["accepted_build"] = None
        project["active_edit"] = None
        project["status"] = "generating"
        project.setdefault("look", {})["cardinals"] = None
        project["look"]["row_9_approved"] = False
        project["look"]["row_9_approval"] = None
        save_project(path, project)
        if variant_rebaselined:
            append_event(
                path,
                "legacy-variant-rebaselined",
                {"parent_id": project["parent_id"], "recorded_at": recorded_at},
            )
        append_event(
            path,
            "project-rebaselined-v2-integrity",
            {"pre_integrity_accepted_build": accepted_build},
        )
        return {
            "ok": True,
            "project": str(path),
            "already_v2": True,
            "integrity_rebaseline": True,
            "variant_integrity_rebaseline": variant_rebaselined,
            "pre_integrity_accepted_build": accepted_build,
        }
    if previous != 1:
        raise ValueError(f"cannot upgrade unsupported contract version: {previous!r}")
    legacy_accepted_build = project.get("accepted_build")
    project["contract_version"] = 2
    project.setdefault("generation", {})["pre_v2_accepted_build"] = legacy_accepted_build
    project["look"] = {
        "mechanics": None,
        "cardinals": None,
        "row_9_approved": False,
        "row_9_approval": None,
    }
    project["status"] = "generating"
    project["accepted_build"] = None
    project["active_edit"] = None
    save_project(path, project)
    append_event(
        path,
        "project-upgraded-v2",
        {
            "from_contract_version": 1,
            "preserved_builds": True,
            "pre_v2_accepted_build": legacy_accepted_build,
        },
    )
    return {"ok": True, "project": str(path), "from_contract_version": 1, "contract_version": 2}
