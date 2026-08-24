from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import petkit.build as build_module
import petkit.project as project_module
from petkit.build import _resolve_install_target, _verify_variant_fork_snapshot, rollback_install
from petkit.contract import load_contract
from petkit.project import (
    TransactionRecoveryError,
    approve_identity,
    atomic_write_json,
    create_variant,
    file_writer_lock,
    init_project,
    load_project,
    project_writer_lock,
    reconcile_pending_operations,
    recover_operation_path,
    remove_operation_marker,
    recorded_authority_values,
    save_project,
    sha256_file,
    source_file_snapshot,
    upgrade_project,
    write_operation_marker,
)
from tests.helpers import identity_image


class SafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repo = Path(__file__).resolve().parents[1]

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def mark_parent_accepted(self, project: Path) -> None:
        _, metadata = load_project(project)
        if metadata["identity"].get("approved") is not True:
            approve_identity(
                project,
                identity_image(self.root / f"{metadata['id']}-identity.png"),
            )
            _, metadata = load_project(project)
        build_id = "build-0001"
        build_dir = project / "builds" / build_id
        build_dir.mkdir(exist_ok=True)
        recorded = recorded_authority_values(metadata)
        authority_fields = (
            "canonical_identity_sha256",
            "mechanics_sha256",
            "cardinals_sha256",
            "look_a_sha256",
            "look_b_sha256",
            "row_9_basis_sha256",
        )
        (build_dir / "build.json").write_text(
            json.dumps(
                {
                    "build_id": build_id,
                    "pet_id": metadata["id"],
                    "build_kind": "release",
                    "source_sha256": source_file_snapshot(project),
                    "build_inputs": {
                        "authority": {field: recorded[field] for field in authority_fields},
                        "chroma_key": metadata["generation"]["chroma_key"],
                        "chroma_threshold": float(metadata["generation"]["chroma_threshold"]),
                    },
                }
            ),
            encoding="utf-8",
        )
        (project / "history" / f"acceptance-{build_id}.json").write_text(
            json.dumps(
                {
                    "build_id": build_id,
                    "visual_qa_confirmed": True,
                }
            ),
            encoding="utf-8",
        )
        metadata["current_build"] = build_id
        metadata["accepted_build"] = build_id
        metadata["active_edit"] = None
        metadata["status"] = "accepted"
        save_project(project, metadata)

    def create_fixture_variant(
        self,
        parent: Path,
        variant_id: str,
        display_name: str,
    ) -> Path:
        """Create a variant from the intentionally minimal synthetic parent fixture."""

        @contextmanager
        def fixture_snapshot(
            _project_dir: Path,
            _project: dict[str, object],
            build_id: str,
            **_kwargs: object,
        ):
            record = json.loads(
                (parent / "builds" / build_id / "build.json").read_text(encoding="utf-8")
            )
            yield parent, record

        with (
            patch("petkit.build.verified_variant_parent_snapshot", fixture_snapshot),
            patch("petkit.build.verify_variant_parent_copy"),
        ):
            return create_variant(
                parent,
                self.root / "variants",
                variant_id,
                display_name,
            )

    def make_package(
        self,
        directory: Path,
        pet_id: str,
        sprite_bytes: bytes,
        *,
        created_at_ns: int | None = None,
        version: int = 2,
    ) -> None:
        directory.mkdir(parents=True)
        (directory / "pet.json").write_text(
            json.dumps(
                {
                    "id": pet_id,
                    "displayName": pet_id,
                    "description": "synthetic package",
                    "spriteVersionNumber": version,
                    "spritesheetPath": "spritesheet.webp",
                }
            ),
            encoding="utf-8",
        )
        (directory / "spritesheet.webp").write_bytes(sprite_bytes)
        if created_at_ns is not None:
            (directory / ".petkit-backup.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "created_at": "2026-01-01T00:00:00Z",
                        "created_at_ns": created_at_ns,
                        "pet_id": pet_id,
                        "files": {
                            "pet.json": sha256_file(directory / "pet.json"),
                            "spritesheet.webp": sha256_file(directory / "spritesheet.webp"),
                        },
                        "provenance": {"tool": "petkit", "operation": "install-displaced"},
                    }
                ),
                encoding="utf-8",
            )

    def make_state_frames(self, project: Path, state_id: str, suffix: str = ".png") -> list[Path]:
        contract = load_contract(2)
        state = contract.state(state_id)
        state_dir = project / "source" / "frames" / state_id
        state_dir.mkdir(parents=True, exist_ok=True)
        paths: list[Path] = []
        for index in range(state.frame_count):
            path = state_dir / f"{index:02d}{suffix}"
            image = Image.new(
                "RGBA",
                (contract.cell_width, contract.cell_height),
                (40 + index * 10, 100, 180, 255),
            )
            if suffix == ".webp":
                image.save(path, format="WEBP", lossless=True)
            else:
                image.save(path, format="PNG")
            paths.append(path)
        return paths

    def test_invalid_reference_does_not_leave_a_partial_project(self) -> None:
        projects = self.root / "pets"
        with self.assertRaisesRegex(ValueError, "reference image does not exist"):
            init_project(
                projects,
                "incomplete",
                "Incomplete",
                "Must not be created.",
                "test",
                "test",
                references=[self.root / "missing.png"],
            )
        self.assertFalse((projects / "incomplete").exists())

    def test_concurrent_same_id_initialization_publishes_exactly_one_project(self) -> None:
        projects = self.root / "pets"
        barrier = threading.Barrier(2)
        real_rename = os.rename
        results: list[Path] = []
        errors: list[Exception] = []

        def synchronized_publish(source: str | Path, destination: str | Path) -> None:
            if Path(destination) == projects / "same-pet":
                barrier.wait(2)
            real_rename(source, destination)

        def initialize(name: str) -> None:
            try:
                results.append(
                    init_project(projects, "same-pet", name, "fixture", "fixture", "fixture")
                )
            except Exception as exc:
                errors.append(exc)

        with patch("petkit.project.os.rename", side_effect=synchronized_publish):
            workers = [threading.Thread(target=initialize, args=(name,)) for name in ("One", "Two")]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(3)

        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ValueError)
        _, project = load_project(projects / "same-pet")
        self.assertIn(project["display_name"], {"One", "Two"})
        events = (projects / "same-pet" / "history" / "events.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(events), 1)
        self.assertEqual(list(projects.glob(".same-pet.init-*")), [])

    def test_import_rejects_a_spritesheet_path_outside_the_package(self) -> None:
        package = self.root / "package"
        package.mkdir()
        (package / "pet.json").write_text(
            json.dumps(
                {
                    "id": "escape",
                    "displayName": "Escape",
                    "description": "Malicious path fixture.",
                    "spriteVersionNumber": 2,
                    "spritesheetPath": "../outside.webp",
                }
            ),
            encoding="utf-8",
        )
        projects = self.root / "pets"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "petkit",
                "import-package",
                "--package",
                str(package),
                "--root",
                str(projects),
            ],
            cwd=self.repo,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("must remain inside", result.stderr)
        self.assertFalse((projects / "escape").exists())

    def test_look_row_scale_is_uniform_project_local_and_non_overwriting(self) -> None:
        project = init_project(self.root / "pets", "scale-test", "Scale Test", "fixture", "fixture", "fixture")
        source = project / "qa" / "row.png"
        Image.new("RGB", (800, 200), (0, 255, 0)).save(source)
        output = project / "qa" / "row-scaled.png"
        command = [
            sys.executable, "-m", "petkit", "scale-look-row-source",
            "--project", str(project), "--state", "look-b", "--strip", str(source),
            "--factor-x", "1.05", "--factor-y", "1.1", "--output", str(output),
        ]
        subprocess.run(command, cwd=self.repo, check=True, capture_output=True, text=True)
        with Image.open(output) as opened:
            self.assertEqual(opened.size, (840, 220))
        repeated = subprocess.run(command, cwd=self.repo, capture_output=True, text=True)
        self.assertEqual(repeated.returncode, 2)
        self.assertIn("refusing to overwrite", repeated.stderr)

    def test_install_overlap_detects_case_aliases_on_case_insensitive_filesystems(self) -> None:
        projects = self.root / "CaseProjects"
        project = init_project(projects, "case-pet", "Case Pet", "fixture", "fixture", "fixture")
        aliased_root = self.root / "caseprojects"
        if not aliased_root.exists() or not aliased_root.samefile(projects):
            self.skipTest("temporary filesystem is case-sensitive")
        with self.assertRaisesRegex(ValueError, "must not be equal or contain one another"):
            _resolve_install_target(project, aliased_root, "case-pet")

    def test_variant_destination_cannot_overlap_its_source_project(self) -> None:
        project = init_project(self.root / "pets", "source-pet", "Source Pet", "fixture", "fixture", "fixture")
        with patch("petkit.project.shutil.copytree") as copytree:
            with self.assertRaisesRegex(ValueError, "must not be equal or contain one another"):
                create_variant(project, project / "source", "nested-variant", "Nested Variant")
            copytree.assert_not_called()
        self.assertFalse((project / "source" / "nested-variant").exists())

    def test_variant_id_must_differ_from_its_parent(self) -> None:
        project = init_project(self.root / "pets", "source-pet", "Source Pet", "fixture", "fixture", "fixture")
        with self.assertRaisesRegex(ValueError, "distinct from its parent"):
            create_variant(project, self.root / "other-root", "source-pet", "Duplicate Identity")

    def test_variant_requires_an_accepted_current_parent_baseline(self) -> None:
        parent = init_project(self.root / "pets", "source-pet", "Source Pet", "fixture", "fixture", "fixture")
        with self.assertRaisesRegex(ValueError, "accepted, current parent baseline"):
            create_variant(parent, self.root / "variants", "unready-child", "Unready Child")
        self.mark_parent_accepted(parent)
        child = self.create_fixture_variant(parent, "ready-child", "Ready Child")
        _, metadata = load_project(child)
        self.assertEqual(metadata["parent_id"], "source-pet")
        self.assertIsNone(metadata["current_build"])

    def test_upgrade_rebaselines_a_legacy_unaccepted_variant_without_losing_work(self) -> None:
        parent = init_project(self.root / "pets", "source-pet", "Source Pet", "fixture", "fixture", "fixture")
        self.mark_parent_accepted(parent)
        variant = self.create_fixture_variant(parent, "legacy-variant", "Legacy Variant")
        retained_work = variant / "source" / "legacy-work.txt"
        retained_work.write_text("retained owner work", encoding="utf-8")
        _, project = load_project(variant)
        project["generation"].pop("fork_snapshot")
        save_project(variant, project)

        result = upgrade_project(variant)

        self.assertTrue(result["variant_integrity_rebaseline"])
        self.assertEqual(retained_work.read_text(encoding="utf-8"), "retained owner work")
        _, upgraded = load_project(variant)
        snapshot = upgraded["generation"]["fork_snapshot"]
        self.assertEqual(snapshot["schema_version"], 2)
        self.assertEqual(
            snapshot["build_parameters"],
            {"chroma_key": "#00FF00", "chroma_threshold": 96.0},
        )
        self.assertEqual(snapshot["origin"], "legacy-owner-rebaseline")
        self.assertIn("legacy-work.txt", snapshot["source_sha256"])
        _verify_variant_fork_snapshot(variant, upgraded)
        retained_work.write_text("changed after rebaseline", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "variant source changed"):
            _verify_variant_fork_snapshot(variant, upgraded)

    def test_variant_snapshot_binds_chroma_parameters(self) -> None:
        parent = init_project(self.root / "pets", "source-pet", "Source Pet", "fixture", "fixture", "fixture")
        self.mark_parent_accepted(parent)
        variant = self.create_fixture_variant(parent, "chroma-variant", "Chroma Variant")
        _, project = load_project(variant)
        original_snapshot = json.loads(json.dumps(project["generation"]["fork_snapshot"]))
        (variant / "source" / "post-fork.txt").write_text("post-fork mutation", encoding="utf-8")
        project["generation"]["chroma_threshold"] = 110.0
        save_project(variant, project)
        with self.assertRaisesRegex(ValueError, "variant source changed"):
            _verify_variant_fork_snapshot(variant, project)
        result = upgrade_project(variant)
        self.assertNotIn("variant_integrity_rebaseline", result)
        _, after_upgrade = load_project(variant)
        self.assertEqual(after_upgrade["generation"]["fork_snapshot"], original_snapshot)
        with self.assertRaisesRegex(ValueError, "variant source changed"):
            _verify_variant_fork_snapshot(variant, after_upgrade)
        (variant / "source" / "post-fork.txt").unlink()
        with self.assertRaisesRegex(ValueError, "variant chroma parameters changed"):
            _verify_variant_fork_snapshot(variant, after_upgrade)

    def test_upgrade_rebaselines_and_clears_an_accepted_legacy_variant_once(self) -> None:
        parent = init_project(self.root / "pets", "source-pet", "Source Pet", "fixture", "fixture", "fixture")
        self.mark_parent_accepted(parent)
        variant = self.create_fixture_variant(parent, "accepted-legacy", "Accepted Legacy")
        _, project = load_project(variant)
        project["generation"]["fork_snapshot"] = {"schema_version": 1}
        build_dir = variant / "builds" / "build-0001"
        build_dir.mkdir()
        (build_dir / "build.json").write_text("{}\n", encoding="utf-8")
        project["current_build"] = "build-0001"
        project["accepted_build"] = "build-0001"
        project["status"] = "accepted"
        save_project(variant, project)

        result = upgrade_project(variant)

        self.assertTrue(result["variant_integrity_rebaseline"])
        self.assertTrue(result["integrity_rebaseline"])
        _, upgraded = load_project(variant)
        self.assertEqual(upgraded["generation"]["fork_snapshot"]["schema_version"], 2)
        self.assertIsNone(upgraded["accepted_build"])
        self.assertEqual(upgraded["generation"]["pre_integrity_accepted_build"], "build-0001")

    def test_identity_reapproval_preserves_identical_and_clears_changed_gates(self) -> None:
        project = init_project(self.root / "pets", "identity-pet", "Identity Pet", "fixture", "fixture", "fixture")
        approve_identity(project, identity_image(self.root / "identity-a.png"))
        _, metadata = load_project(project)
        metadata["look"]["cardinals"] = {"approved": True}
        metadata["look"]["row_9_approved"] = True
        metadata["look"]["row_9_approval"] = {"basis_sha256": "a" * 64}
        metadata["current_build"] = "build-0001"
        metadata["accepted_build"] = "build-0001"
        metadata["status"] = "accepted"
        save_project(project, metadata)

        approve_identity(project, project / metadata["identity"]["canonical_reference"])
        _, unchanged = load_project(project)
        self.assertEqual(unchanged["look"]["cardinals"], {"approved": True})
        self.assertTrue(unchanged["look"]["row_9_approved"])
        self.assertEqual(unchanged["look"]["row_9_approval"], {"basis_sha256": "a" * 64})
        self.assertEqual(unchanged["accepted_build"], "build-0001")
        self.assertEqual(unchanged["status"], "accepted")

        approve_identity(project, identity_image(self.root / "identity-b.png", color=(190, 70, 120)))

        _, changed = load_project(project)
        self.assertIsNone(changed["look"]["cardinals"])
        self.assertFalse(changed["look"]["row_9_approved"])
        self.assertIsNone(changed["look"]["row_9_approval"])
        self.assertIsNone(changed["accepted_build"])
        self.assertEqual(changed["generation"]["pre_identity_accepted_build"], "build-0001")
        self.assertEqual(changed["status"], "identity-approved")

    def test_identity_approval_uses_opened_bytes_when_the_source_path_changes(self) -> None:
        project = init_project(self.root / "pets", "aba-pet", "ABA Pet", "fixture", "fixture", "fixture")
        original = identity_image(self.root / "aba-original.png")
        changed = identity_image(self.root / "aba-changed.png", color=(210, 60, 120))
        original_bytes = original.read_bytes()
        changed_hash = sha256_file(changed)
        approve_identity(project, original)
        _, metadata = load_project(project)
        metadata["look"]["cardinals"] = {"approved": True}
        metadata["look"]["row_9_approved"] = True
        metadata["look"]["row_9_approval"] = {"basis_sha256": "c" * 64}
        metadata["current_build"] = "build-0001"
        metadata["accepted_build"] = "build-0001"
        metadata["status"] = "accepted"
        save_project(project, metadata)
        holder = self.root / "aba-original-holder.png"
        real_open = os.open
        injected = False

        def open_with_path_swap(
            path: str | bytes | os.PathLike[str],
            flags: int,
            *args: object,
            **kwargs: object,
        ) -> int:
            nonlocal injected
            if Path(path) == original and not injected:
                injected = True
                original.replace(holder)
                changed.replace(original)
                descriptor = real_open(path, flags, *args, **kwargs)
                original.replace(changed)
                holder.replace(original)
                return descriptor
            return real_open(path, flags, *args, **kwargs)

        with patch("petkit.project.os.open", side_effect=open_with_path_swap):
            approve_identity(project, original)

        self.assertTrue(injected)
        self.assertEqual(original.read_bytes(), original_bytes)
        _, approved = load_project(project)
        canonical = project / approved["identity"]["canonical_reference"]
        self.assertEqual(sha256_file(canonical), changed_hash)
        self.assertIsNone(approved["look"]["cardinals"])
        self.assertFalse(approved["look"]["row_9_approved"])
        self.assertIsNone(approved["accepted_build"])
        self.assertEqual(approved["generation"]["pre_identity_accepted_build"], "build-0001")

    def test_identity_metadata_failure_restores_the_previous_canonical_image(self) -> None:
        project = init_project(self.root / "pets", "identity-rollback", "Identity Rollback", "fixture", "fixture", "fixture")
        original = identity_image(self.root / "identity-rollback-original.png")
        changed = identity_image(self.root / "identity-rollback-changed.png", color=(205, 65, 125))
        approve_identity(project, original)
        _, metadata = load_project(project)
        metadata["look"]["cardinals"] = {"approved": True}
        metadata["look"]["row_9_approved"] = True
        metadata["look"]["row_9_approval"] = {"basis_sha256": "d" * 64}
        metadata["current_build"] = "build-0001"
        metadata["accepted_build"] = "build-0001"
        metadata["status"] = "accepted"
        save_project(project, metadata)
        _, before_metadata = load_project(project)
        canonical = project / before_metadata["identity"]["canonical_reference"]
        before_bytes = canonical.read_bytes()

        with patch("petkit.project.save_project", side_effect=OSError("injected identity metadata failure")):
            with self.assertRaisesRegex(OSError, "injected identity metadata failure"):
                approve_identity(project, changed)

        _, after_metadata = load_project(project)
        self.assertEqual(after_metadata, before_metadata)
        self.assertEqual(canonical.read_bytes(), before_bytes)
        self.assertEqual(after_metadata["accepted_build"], "build-0001")
        self.assertEqual(after_metadata["look"]["cardinals"], {"approved": True})
        self.assertTrue(after_metadata["look"]["row_9_approved"])
        approved_root = project / "references" / "approved"
        self.assertEqual(list(approved_root.glob(".canonical-base*.preapproval-*")), [])
        self.assertEqual(list(approved_root.glob(".canonical-base*.staging-*")), [])

    def test_identity_final_hash_failure_restores_the_previous_canonical_image(self) -> None:
        project = init_project(self.root / "pets", "identity-final-hash", "Identity Final Hash", "fixture", "fixture", "fixture")
        original = identity_image(self.root / "identity-final-original.png")
        changed = identity_image(self.root / "identity-final-changed.png", color=(200, 70, 130))
        approve_identity(project, original)
        _, before_metadata = load_project(project)
        canonical = project / before_metadata["identity"]["canonical_reference"]
        before_bytes = canonical.read_bytes()
        real_sha256 = sha256_file
        injected = False

        def fail_final_hash(path: str | Path) -> str:
            nonlocal injected
            if Path(path) == canonical and not injected:
                injected = True
                return "f" * 64
            return real_sha256(Path(path))

        with patch("petkit.project.sha256_file", side_effect=fail_final_hash):
            with self.assertRaisesRegex(ValueError, "canonical identity target no longer matches"):
                approve_identity(project, changed)

        self.assertTrue(injected)
        _, after_metadata = load_project(project)
        self.assertEqual(after_metadata, before_metadata)
        self.assertEqual(canonical.read_bytes(), before_bytes)
        approved_root = project / "references" / "approved"
        self.assertEqual(list(approved_root.glob(".canonical-base*.preapproval-*")), [])

    def test_identity_displaced_image_cleanup_failure_is_a_post_commit_warning(self) -> None:
        project = init_project(self.root / "pets", "identity-warning", "Identity Warning", "fixture", "fixture", "fixture")
        original = identity_image(self.root / "identity-warning-original.png")
        changed = identity_image(self.root / "identity-warning-changed.png", color=(195, 75, 135))
        approve_identity(project, original)
        real_unlink = Path.unlink
        retained: list[Path] = []

        def fail_displaced_unlink(path: Path, *args: object, **kwargs: object) -> None:
            candidate = Path(path)
            if ".preapproval-" in candidate.name:
                retained.append(candidate)
                raise OSError("injected displaced identity cleanup failure")
            real_unlink(candidate, *args, **kwargs)

        with patch("petkit.project.Path.unlink", autospec=True, side_effect=fail_displaced_unlink):
            result = approve_identity(project, changed)

        self.assertEqual(len(retained), 1)
        self.assertTrue(retained[0].is_file())
        self.assertEqual(len(result["post_commit_warnings"]), 1)
        self.assertIn(str(retained[0]), result["post_commit_warnings"][0])
        _, committed = load_project(project)
        canonical = project / committed["identity"]["canonical_reference"]
        self.assertEqual(committed["identity"]["canonical_sha256"], sha256_file(canonical))
        events = (project / "history" / "events.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(json.loads(events[-1])["event"], "identity-approved")
        retained[0].unlink()

    def test_identity_cancellation_reconciles_every_precommit_phase(self) -> None:
        project = init_project(self.root / "pets", "identity-cancel", "Identity Cancel", "fixture", "fixture", "fixture")
        original = identity_image(self.root / "identity-cancel-original.png")
        changed = identity_image(self.root / "identity-cancel-changed.png", color=(185, 80, 145))
        approve_identity(project, original)
        _, metadata = load_project(project)
        metadata["look"]["cardinals"] = {"approved": True}
        metadata["look"]["row_9_approved"] = True
        metadata["look"]["row_9_approval"] = {"basis_sha256": "e" * 64}
        metadata["current_build"] = "build-0001"
        metadata["accepted_build"] = "build-0001"
        metadata["status"] = "accepted"
        save_project(project, metadata)
        _, before_metadata = load_project(project)
        canonical = project / before_metadata["identity"]["canonical_reference"]
        before_bytes = canonical.read_bytes()
        real_replace = project_module.os.replace
        real_copy2 = shutil.copy2
        phases = (
            "after-mkdir",
            "before-copy",
            "during-work",
            "before-publish",
            "after-publish",
            "metadata-temp-create",
            "metadata-temp-write",
            "before-save",
        )

        for phase in phases:
            with self.subTest(phase=phase):
                snapshots: list[Path] = []

                def capture_trace(event: str, **details: object) -> None:
                    if event == "identity.after-snapshot-mkdir":
                        snapshots.append(Path(str(details["path"])))
                        if phase == "after-mkdir":
                            raise KeyboardInterrupt("injected after-mkdir")
                    metadata_phase = {
                        "metadata-temp-create": "json.after-temp-create",
                        "metadata-temp-write": "json.after-temp-write",
                    }.get(phase)
                    if (
                        metadata_phase == event
                        and details.get("path") == str(project / "pet-project.json")
                    ):
                        raise KeyboardInterrupt(f"injected {phase}")

                def interrupt_copy2(source: str | Path, destination: str | Path, *args: object, **kwargs: object) -> object:
                    if phase == "during-work":
                        raise KeyboardInterrupt("injected during-work")
                    return real_copy2(source, destination, *args, **kwargs)

                def interrupt_publish(source: str | Path, destination: str | Path) -> None:
                    source_path = Path(source)
                    destination_path = Path(destination)
                    if destination_path == canonical and ".staging-" in source_path.name:
                        if phase == "after-publish":
                            real_replace(source, destination)
                        raise KeyboardInterrupt(f"injected {phase}")
                    real_replace(source, destination)

                with ExitStack() as stack:
                    stack.enter_context(patch("petkit.project.transaction_trace", side_effect=capture_trace))
                    if phase == "after-mkdir":
                        pass
                    elif phase == "before-copy":
                        stack.enter_context(
                            patch("petkit.project.shutil.copyfileobj", side_effect=KeyboardInterrupt("injected before-copy"))
                        )
                    elif phase == "during-work":
                        stack.enter_context(patch("petkit.project.shutil.copy2", side_effect=interrupt_copy2))
                    elif phase in {"before-publish", "after-publish"}:
                        stack.enter_context(patch("petkit.project.os.replace", side_effect=interrupt_publish))
                    elif phase == "before-save":
                        stack.enter_context(
                            patch("petkit.project.save_project", side_effect=KeyboardInterrupt("injected before-save"))
                        )
                    with self.assertRaises(KeyboardInterrupt) as raised:
                        approve_identity(project, changed)

                self.assertEqual(str(raised.exception), f"injected {phase}")
                _, durable = load_project(project)
                self.assertEqual(durable, before_metadata)
                self.assertEqual(canonical.read_bytes(), before_bytes)
                approved_root = project / "references" / "approved"
                self.assertEqual(list(approved_root.glob(".canonical-base*.*-*")), [])
                self.assertTrue(snapshots)
                self.assertTrue(all(not path.exists() for path in snapshots))
                with project_writer_lock(project):
                    pass

    def test_identity_cancellation_after_durable_save_preserves_consistent_commit(self) -> None:
        project = init_project(self.root / "pets", "identity-postcommit", "Identity Postcommit", "fixture", "fixture", "fixture")
        original = identity_image(self.root / "identity-postcommit-original.png")
        changed = identity_image(self.root / "identity-postcommit-changed.png", color=(180, 85, 150))
        approve_identity(project, original)
        _, metadata = load_project(project)
        metadata["look"]["cardinals"] = {"approved": True}
        metadata["look"]["row_9_approved"] = True
        metadata["look"]["row_9_approval"] = {"basis_sha256": "f" * 64}
        metadata["current_build"] = "build-0001"
        metadata["accepted_build"] = "build-0001"
        metadata["status"] = "accepted"
        save_project(project, metadata)
        real_save = project_module.save_project

        def save_then_interrupt(*args: object, **kwargs: object) -> None:
            real_save(*args, **kwargs)
            raise KeyboardInterrupt("injected identity after-save")

        with patch("petkit.project.save_project", side_effect=save_then_interrupt):
            with self.assertRaises(KeyboardInterrupt) as raised:
                approve_identity(project, changed)

        _, durable = load_project(project)
        canonical = project / durable["identity"]["canonical_reference"]
        self.assertEqual(durable["identity"]["canonical_sha256"], sha256_file(canonical))
        self.assertEqual(sha256_file(canonical), sha256_file(changed))
        self.assertIsNone(durable["accepted_build"])
        self.assertIsNone(durable["look"]["cardinals"])
        self.assertTrue(any("durably committed" in note for note in raised.exception.__notes__))
        self.assertEqual(list((project / "references" / "approved").glob(".canonical-base*.*-*")), [])
        with project_writer_lock(project):
            pass

    def test_identity_cancellation_aggregates_private_cleanup_failure(self) -> None:
        project = init_project(self.root / "pets", "identity-cleanup", "Identity Cleanup", "fixture", "fixture", "fixture")
        original = identity_image(self.root / "identity-cleanup-original.png")
        changed = identity_image(self.root / "identity-cleanup-changed.png", color=(175, 90, 155))
        approve_identity(project, original)
        _, before_metadata = load_project(project)
        canonical = project / before_metadata["identity"]["canonical_reference"]
        before_bytes = canonical.read_bytes()
        real_rmtree = shutil.rmtree
        real_replace = project_module.os.replace
        quarantines: list[Path] = []

        def fail_private_cleanup(path: str | Path, *args: object, **kwargs: object) -> None:
            candidate = Path(path)
            if "petkit-identity-" in candidate.name:
                raise OSError("injected private identity cleanup failure")
            real_rmtree(path, *args, **kwargs)

        def capture_quarantine(source: str | Path, destination: str | Path) -> None:
            target = Path(destination)
            if "petkit-identity-" in target.name and target.name.endswith(".recovery"):
                quarantines.append(target)
            real_replace(source, destination)

        try:
            with (
                patch("petkit.project.shutil.copyfileobj", side_effect=KeyboardInterrupt("injected identity cancellation")),
                patch("petkit.project.shutil.rmtree", side_effect=fail_private_cleanup),
                patch("petkit.project.os.replace", side_effect=capture_quarantine),
            ):
                with self.assertRaises(TransactionRecoveryError) as raised:
                    approve_identity(project, changed)
            self.assertIsInstance(raised.exception.__cause__, KeyboardInterrupt)
            self.assertEqual(len(quarantines), 1)
            self.assertTrue(quarantines[0].exists())
            self.assertIn(str(quarantines[0]), str(raised.exception))
            _, durable = load_project(project)
            self.assertEqual(durable, before_metadata)
            self.assertEqual(canonical.read_bytes(), before_bytes)
            with project_writer_lock(project):
                reconcile_pending_operations(project)
            self.assertTrue(all(not quarantine.exists() for quarantine in quarantines))
            self.assertEqual(list((project / ".petkit-recovery").glob("*.json")), [])
        finally:
            for quarantine in quarantines:
                if quarantine.exists():
                    real_rmtree(quarantine)

    def test_failed_identity_restore_preserves_the_only_prior_bytes_and_retries_from_marker(self) -> None:
        project = init_project(self.root / "pets", "identity-retry", "Identity Retry", "fixture", "fixture", "fixture")
        original = identity_image(self.root / "identity-retry-original.png")
        changed = identity_image(self.root / "identity-retry-changed.png", color=(170, 95, 160))
        approve_identity(project, original)
        _, before = load_project(project)
        canonical = project / before["identity"]["canonical_reference"]
        original_bytes = canonical.read_bytes()
        real_replace = project_module.os.replace

        def fail_only_restore(source: str | Path, destination: str | Path) -> None:
            source_path = Path(source)
            if ".preapproval-" in source_path.name and Path(destination) == canonical:
                raise OSError("injected restore refusal")
            real_replace(source, destination)

        with (
            patch("petkit.project.save_project", side_effect=KeyboardInterrupt("injected metadata cancellation")),
            patch("petkit.project.os.replace", side_effect=fail_only_restore),
        ):
            with self.assertRaises(TransactionRecoveryError) as raised:
                approve_identity(project, changed)

        self.assertIsInstance(raised.exception.__cause__, KeyboardInterrupt)
        displaced = list((project / "references" / "approved").glob(".canonical-base*.preapproval-*"))
        self.assertEqual(len(displaced), 1)
        self.assertEqual(displaced[0].read_bytes(), original_bytes)
        self.assertIn(str(displaced[0]), str(raised.exception))
        self.assertTrue(list((project / ".petkit-recovery").glob("*.json")))
        _, mismatched = load_project(project)
        self.assertEqual(mismatched, before)
        self.assertNotEqual(sha256_file(canonical), before["identity"]["canonical_sha256"])

        with project_writer_lock(project):
            reconcile_pending_operations(project)
        _, restored = load_project(project)
        self.assertEqual(restored, before)
        self.assertEqual(canonical.read_bytes(), original_bytes)
        self.assertEqual(list((project / ".petkit-recovery").glob("*.json")), [])

    def test_atomic_json_cancellation_has_deterministic_recovery_paths(self) -> None:
        target = self.root / "atomic" / "metadata.json"
        atomic_write_json(target, {"value": "old"}, operation_id="initial")
        phases = ("json.after-temp-create", "json.after-temp-write", "json.before-replace", "json.after-replace")
        for phase in phases:
            with self.subTest(phase=phase):
                atomic_write_json(target, {"value": "old"}, operation_id=f"reset-{phase.replace('.', '-')}")

                def interrupt(event: str, **details: object) -> None:
                    if event == phase and details.get("path") == str(target):
                        raise KeyboardInterrupt(f"injected {phase}")

                with patch("petkit.project.transaction_trace", side_effect=interrupt):
                    with self.assertRaises(KeyboardInterrupt) as raised:
                        atomic_write_json(target, {"value": "new"}, operation_id="phase-test")
                self.assertEqual(str(raised.exception), f"injected {phase}")
                expected = "new" if phase == "json.after-replace" else "old"
                self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["value"], expected)
                self.assertEqual(list(target.parent.glob(".metadata.json.write-phase-test.*")), [])

        real_unlink = Path.unlink

        def fail_registered_cleanup(path: Path, *args: object, **kwargs: object) -> None:
            candidate = Path(path)
            if ".metadata.json.write-cleanup-failure." in candidate.name:
                raise OSError("injected atomic temp cleanup failure")
            real_unlink(candidate, *args, **kwargs)

        def interrupt_after_create(event: str, **details: object) -> None:
            if event == "json.after-temp-create" and details.get("path") == str(target):
                raise SystemExit("injected atomic cancellation")

        with (
            patch("petkit.project.transaction_trace", side_effect=interrupt_after_create),
            patch("petkit.project.Path.unlink", autospec=True, side_effect=fail_registered_cleanup),
        ):
            with self.assertRaises(TransactionRecoveryError) as raised:
                atomic_write_json(target, {"value": "uncommitted"}, operation_id="cleanup-failure")
        self.assertIsInstance(raised.exception.__cause__, SystemExit)
        recovery = target.parent / ".metadata.json.write-cleanup-failure.recovery"
        self.assertTrue(recovery.exists())
        self.assertIn(str(recovery), str(raised.exception))
        atomic_write_json(target, {"value": "recovered"}, operation_id="cleanup-failure")
        self.assertFalse(recovery.exists())

    def test_cleanup_reconciles_cancellation_immediately_after_quarantine_rename(self) -> None:
        original = self.root / "private-operation"
        recovery = self.root / ".private-operation.recovery"
        original.mkdir()
        (original / "private.txt").write_text("private", encoding="utf-8")
        real_replace = project_module.os.replace

        def rename_then_interrupt(source: str | Path, destination: str | Path) -> None:
            real_replace(source, destination)
            if Path(destination) == recovery:
                raise KeyboardInterrupt("injected after quarantine rename")

        with patch("petkit.project.os.replace", side_effect=rename_then_interrupt):
            with self.assertRaises(KeyboardInterrupt) as raised:
                recover_operation_path(
                    original,
                    "private operation",
                    quarantine=recovery,
                    quarantine_first=True,
                )
        self.assertEqual(str(raised.exception), "injected after quarantine rename")
        self.assertFalse(original.exists())
        self.assertFalse(recovery.exists())

        original.mkdir()
        (original / "private.txt").write_text("private", encoding="utf-8")
        real_rmtree = project_module.shutil.rmtree

        def fail_recovery_cleanup(path: str | Path, *args: object, **kwargs: object) -> None:
            if Path(path) == recovery:
                raise OSError("injected recovery cleanup failure")
            real_rmtree(path, *args, **kwargs)

        with (
            patch("petkit.project.os.replace", side_effect=rename_then_interrupt),
            patch("petkit.project.shutil.rmtree", side_effect=fail_recovery_cleanup),
        ):
            errors = recover_operation_path(
                original,
                "private operation",
                quarantine=recovery,
                quarantine_first=True,
                defer_cancellation=True,
            )
        self.assertTrue(recovery.exists())
        self.assertTrue(any(str(recovery) in error for error in errors))
        real_rmtree(recovery)

    def test_next_mutation_discovers_a_marker_moved_to_its_recovery_path(self) -> None:
        project = init_project(self.root / "pets", "marker-retry", "Marker Retry", "fixture", "fixture", "fixture")
        _, original = load_project(project)
        operation_id = "a" * 32
        marker = write_operation_marker(
            project,
            {
                "schema_version": 1,
                "kind": "identity",
                "operation_id": operation_id,
                "original_project": original,
                "target": str(project / "references" / "approved" / "canonical-base.png"),
                "relative": "references/approved/canonical-base.png",
                "snapshot_sha256": None,
            },
        )
        marker_recovery = marker.with_name(f".{marker.name}.cleanup")
        real_unlink = Path.unlink

        def fail_marker_cleanup(path: Path, *args: object, **kwargs: object) -> None:
            candidate = Path(path)
            if candidate in {marker, marker_recovery}:
                raise OSError("injected marker cleanup failure")
            real_unlink(candidate, *args, **kwargs)

        with patch("petkit.project.Path.unlink", autospec=True, side_effect=fail_marker_cleanup):
            errors = remove_operation_marker(marker)
        self.assertTrue(errors)
        self.assertFalse(marker.exists())
        self.assertTrue(marker_recovery.exists())
        self.assertTrue(any(str(marker_recovery) in error for error in errors))

        save_project(project, original)
        self.assertFalse(marker.exists())
        self.assertFalse(marker_recovery.exists())

    def test_writer_lock_cancellation_boundaries_never_poison_reuse(self) -> None:
        lock_path = self.root / "lock-phases" / "writer.lock"
        phases = (
            "lock.after-open",
            "lock.after-flock",
            "lock.after-state-publication",
            "lock.before-state-removal",
            "lock.after-state-removal",
            "lock.before-unlock",
            "lock.after-unlock",
            "lock.before-close",
            "lock.after-close",
        )
        real_flock = project_module.fcntl.flock
        for phase in phases:
            with self.subTest(phase=phase):
                def interrupt(event: str, **_details: object) -> None:
                    if event == phase:
                        raise KeyboardInterrupt(f"injected {phase}")

                with patch("petkit.project.transaction_trace", side_effect=interrupt):
                    with self.assertRaises(KeyboardInterrupt) as raised:
                        with file_writer_lock(lock_path):
                            pass
                self.assertEqual(str(raised.exception), f"injected {phase}")
                self.assertFalse(getattr(project_module._LOCK_STATE, "held", {}))

                acquisitions: list[int] = []

                def count_flock(descriptor: int, operation: int) -> None:
                    if operation == project_module.fcntl.LOCK_EX:
                        acquisitions.append(descriptor)
                    real_flock(descriptor, operation)

                with patch("petkit.project.fcntl.flock", side_effect=count_flock):
                    with file_writer_lock(lock_path):
                        pass
                self.assertEqual(len(acquisitions), 1)

        stale_descriptor = os.open(lock_path, os.O_RDWR)
        os.close(stale_descriptor)
        project_module._LOCK_STATE.held = {
            str(lock_path.absolute()): {"depth": 1, "descriptor": stale_descriptor, "valid": True}
        }
        acquisitions = []

        def count_real_flock(descriptor: int, operation: int) -> None:
            if operation == project_module.fcntl.LOCK_EX:
                acquisitions.append(descriptor)
            real_flock(descriptor, operation)

        with patch("petkit.project.fcntl.flock", side_effect=count_real_flock):
            with file_writer_lock(lock_path):
                pass
        self.assertEqual(len(acquisitions), 1)
        self.assertFalse(getattr(project_module._LOCK_STATE, "held", {}))

        def fail_unlock(descriptor: int, operation: int) -> None:
            if operation == project_module.fcntl.LOCK_UN:
                raise OSError("injected unlock failure")
            real_flock(descriptor, operation)

        with patch("petkit.project.fcntl.flock", side_effect=fail_unlock):
            with self.assertRaisesRegex(TransactionRecoveryError, "unlock failed"):
                with file_writer_lock(lock_path):
                    pass
        self.assertFalse(getattr(project_module._LOCK_STATE, "held", {}))
        with file_writer_lock(lock_path):
            pass

        leaked_descriptors: list[int] = []

        def fail_close(descriptor: int) -> None:
            leaked_descriptors.append(descriptor)
            raise OSError("injected close failure")

        with patch("petkit.project.os.close", side_effect=fail_close):
            with self.assertRaisesRegex(TransactionRecoveryError, "descriptor close failed"):
                with file_writer_lock(lock_path):
                    pass
        self.assertEqual(len(leaked_descriptors), 1)
        self.assertFalse(getattr(project_module._LOCK_STATE, "held", {}))
        os.close(leaked_descriptors[0])
        with file_writer_lock(lock_path):
            pass

    def test_identical_prebaseline_variant_identity_reapproval_preserves_fork_authority(self) -> None:
        parent = init_project(self.root / "pets", "source-pet", "Source Pet", "fixture", "fixture", "fixture")
        approve_identity(parent, identity_image(self.root / "stable-parent-identity.png"))
        _, parent_metadata = load_project(parent)
        parent_metadata["look"]["cardinals"] = {"approved": True}
        parent_metadata["look"]["row_9_approved"] = True
        parent_metadata["look"]["row_9_approval"] = {"basis_sha256": "b" * 64}
        save_project(parent, parent_metadata)
        self.mark_parent_accepted(parent)
        variant = self.create_fixture_variant(parent, "stable-child", "Stable Child")
        _, metadata = load_project(variant)

        approve_identity(variant, variant / metadata["identity"]["canonical_reference"])

        _, unchanged = load_project(variant)
        self.assertEqual(unchanged["look"]["cardinals"], {"approved": True})
        self.assertTrue(unchanged["look"]["row_9_approved"])
        self.assertEqual(unchanged["look"]["row_9_approval"], {"basis_sha256": "b" * 64})
        _verify_variant_fork_snapshot(variant, unchanged)

    def test_status_does_not_create_review_or_other_project_state(self) -> None:
        project = init_project(self.root / "pets", "status-pet", "Status Pet", "fixture", "fixture", "fixture")
        before = sorted(path.relative_to(project).as_posix() for path in project.rglob("*"))
        result = subprocess.run(
            [sys.executable, "-m", "petkit", "status", "--project", str(project)],
            cwd=self.repo,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        after = sorted(path.relative_to(project).as_posix() for path in project.rglob("*"))
        self.assertEqual(after, before)
        self.assertFalse((project / "reviews").exists())

    def test_nested_output_symlinks_and_irregular_row_backups_are_rejected(self) -> None:
        guide_project = init_project(self.root / "pets", "guide-pet", "Guide Pet", "fixture", "fixture", "fixture")
        outside_guides = self.root / "outside-guides"
        outside_guides.mkdir()
        (guide_project / "qa" / "layout-guides").symlink_to(outside_guides, target_is_directory=True)
        guides = subprocess.run(
            [sys.executable, "-m", "petkit", "make-guides", "--project", str(guide_project)],
            cwd=self.repo,
            capture_output=True,
            text=True,
        )
        self.assertEqual(guides.returncode, 2)
        self.assertIn("symbolic", guides.stderr)
        self.assertEqual(list(outside_guides.iterdir()), [])

        frame_project = init_project(self.root / "pets", "frame-pet", "Frame Pet", "fixture", "fixture", "fixture")
        self.make_state_frames(frame_project, "idle")
        outside_backups = self.root / "outside-frame-backups"
        outside_backups.mkdir()
        (frame_project / "history" / "frame-backups").symlink_to(outside_backups, target_is_directory=True)
        replacement = identity_image(self.root / "replacement.png")
        replace = subprocess.run(
            [
                sys.executable,
                "-m",
                "petkit",
                "replace-frame",
                "--project",
                str(frame_project),
                "--state",
                "idle",
                "--index",
                "0",
                "--image",
                str(replacement),
            ],
            cwd=self.repo,
            capture_output=True,
            text=True,
        )
        self.assertEqual(replace.returncode, 2)
        self.assertIn("symbolic", replace.stderr)
        self.assertEqual(list(outside_backups.iterdir()), [])

        row_project = init_project(self.root / "pets", "row-pet", "Row Pet", "fixture", "fixture", "fixture")
        current = self.make_state_frames(row_project, "idle")
        current_hash = sha256_file(current[0])
        backup = row_project / "history" / "row-backups" / "bad-idle"
        backup.mkdir(parents=True)
        for source in current:
            target = backup / source.name
            target.write_bytes(source.read_bytes())
        (backup / "unexpected").mkdir()
        restore = subprocess.run(
            [
                sys.executable,
                "-m",
                "petkit",
                "restore-row",
                "--project",
                str(row_project),
                "--state",
                "idle",
                "--backup",
                str(backup),
            ],
            cwd=self.repo,
            capture_output=True,
            text=True,
        )
        self.assertEqual(restore.returncode, 2)
        self.assertIn("irregular or unsupported descendant", restore.stderr)
        self.assertEqual(sha256_file(current[0]), current_hash)

        (backup / "unexpected").rmdir()
        external_frame = self.root / "external-frame.png"
        external_frame.write_bytes(current[0].read_bytes())
        (backup / "00.png").unlink()
        (backup / "00.png").symlink_to(external_frame)
        linked_restore = subprocess.run(
            [
                sys.executable,
                "-m",
                "petkit",
                "restore-row",
                "--project",
                str(row_project),
                "--state",
                "idle",
                "--backup",
                str(backup),
            ],
            cwd=self.repo,
            capture_output=True,
            text=True,
        )
        self.assertEqual(linked_restore.returncode, 2)
        self.assertIn("symbolic", linked_restore.stderr)
        self.assertEqual(sha256_file(current[0]), current_hash)

    def test_webp_frame_replace_and_restore_are_reversible_and_unambiguous(self) -> None:
        project = init_project(self.root / "pets", "webp-pet", "WebP Pet", "fixture", "fixture", "fixture")
        frames = self.make_state_frames(project, "idle", ".webp")
        original = frames[0].read_bytes()
        replacement = identity_image(self.root / "webp-replacement.png", color=(220, 80, 100))
        replace = subprocess.run(
            [
                sys.executable,
                "-m",
                "petkit",
                "replace-frame",
                "--project",
                str(project),
                "--state",
                "idle",
                "--index",
                "0",
                "--image",
                str(replacement),
            ],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        )
        record = json.loads(replace.stdout)
        backup = Path(record["backup"])
        self.assertEqual(Path(record["frame"]).suffix, ".webp")
        self.assertEqual(backup.suffix, ".webp")
        self.assertNotEqual(frames[0].read_bytes(), original)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "petkit",
                "restore-frame",
                "--project",
                str(project),
                "--state",
                "idle",
                "--index",
                "0",
                "--backup",
                str(backup),
            ],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(frames[0].read_bytes(), original)

        row_backup = project / "history" / "row-backups" / "webp-idle"
        row_backup.mkdir(parents=True)
        for frame in frames:
            (row_backup / frame.name).write_bytes(frame.read_bytes())
        restored_row = subprocess.run(
            [
                sys.executable,
                "-m",
                "petkit",
                "restore-row",
                "--project",
                str(project),
                "--state",
                "idle",
                "--backup",
                str(row_backup),
            ],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        )
        row_record = json.loads(restored_row.stdout)
        self.assertEqual(frames[0].read_bytes(), original)
        displaced = Path(row_record["displaced_backup"])
        self.assertEqual({path.suffix for path in displaced.iterdir()}, {".webp"})

        duplicate = frames[0].with_suffix(".png")
        contract = load_contract(2)
        Image.new(
            "RGBA",
            (contract.cell_width, contract.cell_height),
            (255, 0, 0, 255),
        ).save(duplicate)
        ambiguous = subprocess.run(
            [
                sys.executable,
                "-m",
                "petkit",
                "replace-frame",
                "--project",
                str(project),
                "--state",
                "idle",
                "--index",
                "0",
                "--image",
                str(replacement),
            ],
            cwd=self.repo,
            capture_output=True,
            text=True,
        )
        self.assertEqual(ambiguous.returncode, 2)
        self.assertIn("ambiguous PNG/WebP", ambiguous.stderr)

    def test_restore_row_rejects_undecodable_and_wrong_sized_images_without_mutation(self) -> None:
        project = init_project(self.root / "pets", "invalid-row", "Invalid Row", "fixture", "fixture", "fixture")
        frames = self.make_state_frames(project, "idle")
        before_frames = {path.name: sha256_file(path) for path in frames}
        before_history = sorted(path.relative_to(project).as_posix() for path in (project / "history").rglob("*"))
        backup = project / "history" / "row-backups" / "invalid-idle"
        backup.mkdir(parents=True)
        for frame in frames:
            (backup / frame.name).write_bytes(frame.read_bytes())
        (backup / "00.png").write_text("not an image", encoding="utf-8")
        fixture_history = sorted(path.relative_to(project).as_posix() for path in (project / "history").rglob("*"))

        def restore() -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "petkit",
                    "restore-row",
                    "--project",
                    str(project),
                    "--state",
                    "idle",
                    "--backup",
                    str(backup),
                ],
                cwd=self.repo,
                capture_output=True,
                text=True,
            )

        unreadable = restore()
        self.assertEqual(unreadable.returncode, 2)
        self.assertIn("unreadable image", unreadable.stderr)
        self.assertEqual({path.name: sha256_file(path) for path in frames}, before_frames)
        self.assertEqual(
            sorted(path.relative_to(project).as_posix() for path in (project / "history").rglob("*")),
            fixture_history,
        )

        Image.new("RGBA", (32, 32), (20, 30, 40, 255)).save(backup / "00.png", format="PNG")
        wrong_size = restore()
        self.assertEqual(wrong_size.returncode, 2)
        self.assertIn("wrong dimensions", wrong_size.stderr)
        self.assertEqual({path.name: sha256_file(path) for path in frames}, before_frames)
        self.assertEqual(
            sorted(path.relative_to(project).as_posix() for path in (project / "history").rglob("*")),
            fixture_history,
        )
        self.assertTrue(set(before_history).issubset(fixture_history))

    def test_project_writer_lock_serializes_mutating_commands(self) -> None:
        project = init_project(self.root / "pets", "locked-pet", "Locked Pet", "fixture", "fixture", "fixture")
        replacement = identity_image(self.root / "replacement.png")
        started = threading.Event()
        finished = threading.Event()

        def mutate() -> None:
            started.set()
            approve_identity(project, replacement)
            finished.set()

        with project_writer_lock(project):
            worker = threading.Thread(target=mutate)
            worker.start()
            self.assertTrue(started.wait(1))
            time.sleep(0.05)
            self.assertFalse(finished.is_set())
        worker.join(2)
        self.assertTrue(finished.is_set())

    def test_source_and_internal_root_symlinks_are_rejected(self) -> None:
        project = init_project(self.root / "pets", "symlink-pet", "Symlink Pet", "fixture", "fixture", "fixture")
        self.mark_parent_accepted(project)
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "00.png").write_bytes(b"outside")
        state = project / "source" / "frames" / "idle"
        state.symlink_to(outside, target_is_directory=True)
        status = subprocess.run(
            [sys.executable, "-m", "petkit", "status", "--project", str(project)],
            cwd=self.repo,
            capture_output=True,
            text=True,
        )
        self.assertEqual(status.returncode, 2)
        self.assertIn("must not be symbolic", status.stderr)
        with self.assertRaisesRegex(ValueError, "symbolic links"):
            self.create_fixture_variant(project, "safe-child", "Safe Child")

        state.unlink()
        backup_root = project / "backups" / "installed"
        backup_root.rmdir()
        backup_root.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "must not be symbolic"):
            rollback_install(project, self.root / "installed")

        install_alias = self.root / "installed-alias"
        install_alias.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "install root must not be a symbolic link"):
            _resolve_install_target(project, install_alias, "symlink-pet")

    def test_rollback_accepts_legacy_backups_and_ignores_extra_symlinks(self) -> None:
        project = init_project(self.root / "pets", "legacy-pet", "Legacy Pet", "fixture", "fixture", "fixture")
        target_root = self.root / "installed"
        self.make_package(target_root / "legacy-pet", "legacy-pet", b"current")
        backup = project / "backups" / "installed" / "historical"
        self.make_package(backup, "legacy-pet", b"legacy", version=1)
        outside = self.root / "outside.txt"
        outside.write_text("outside", encoding="utf-8")
        (backup / "ignored-link").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "explicit legacy opt-in"):
            rollback_install(project, target_root, backup)
        result = rollback_install(project, target_root, backup, allow_legacy_backup=True)
        self.assertTrue(result["ok"])
        self.assertEqual((target_root / "legacy-pet" / "spritesheet.webp").read_bytes(), b"legacy")
        self.assertFalse((target_root / "legacy-pet" / "ignored-link").exists())

    def test_legacy_rollback_requires_a_manifest_for_the_copied_atlas(self) -> None:
        project = init_project(self.root / "pets", "legacy-pet", "Legacy Pet", "fixture", "fixture", "fixture")
        target_root = self.root / "installed"
        self.make_package(target_root / "legacy-pet", "legacy-pet", b"current")
        backup = project / "backups" / "installed" / "bad-historical"
        self.make_package(backup, "legacy-pet", b"legacy", version=1)
        manifest = json.loads((backup / "pet.json").read_text(encoding="utf-8"))
        manifest["spritesheetPath"] = "missing.webp"
        (backup / "pet.json").write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "reference the copied spritesheet.webp"):
            rollback_install(project, target_root, backup, allow_legacy_backup=True)
        self.assertEqual((target_root / "legacy-pet" / "spritesheet.webp").read_bytes(), b"current")

    def test_rollback_verifies_the_staged_copy_after_copying(self) -> None:
        project = init_project(self.root / "pets", "race-pet", "Race Pet", "fixture", "fixture", "fixture")
        target_root = self.root / "installed"
        self.make_package(target_root / "race-pet", "race-pet", b"current")
        backup = project / "backups" / "installed" / "recorded"
        self.make_package(backup, "race-pet", b"recorded", created_at_ns=100)
        real_copy = build_module._copy_installed_package
        corrupted = False

        def corrupt_staged(source: Path, destination: Path) -> dict[str, str]:
            nonlocal corrupted
            hashes = real_copy(source, destination)
            if source == backup and not corrupted:
                (destination / "spritesheet.webp").write_bytes(b"changed-after-copy")
                corrupted = True
            return hashes

        with patch("petkit.build._copy_installed_package", side_effect=corrupt_staged):
            with self.assertRaisesRegex(ValueError, "recorded integrity hashes"):
                rollback_install(project, target_root, backup)
        self.assertEqual((target_root / "race-pet" / "spritesheet.webp").read_bytes(), b"current")

    def test_rollback_rejects_unsafe_history_before_swapping_installed_bytes(self) -> None:
        project = init_project(self.root / "pets", "history-pet", "History Pet", "fixture", "fixture", "fixture")
        target_root = self.root / "installed"
        self.make_package(target_root / "history-pet", "history-pet", b"current")
        backup = project / "backups" / "installed" / "recorded"
        self.make_package(backup, "history-pet", b"recorded", created_at_ns=100)
        history = project / "history"
        retained_history = project / "history-retained"
        outside_history = self.root / "outside-history"
        outside_history.mkdir()
        history.rename(retained_history)
        history.symlink_to(outside_history, target_is_directory=True)
        try:
            with self.assertRaisesRegex(ValueError, "must not be symbolic"):
                rollback_install(project, target_root, backup)
        finally:
            history.unlink()
            retained_history.rename(history)
        self.assertEqual((target_root / "history-pet" / "spritesheet.webp").read_bytes(), b"current")

    def test_automatic_rollback_uses_recorded_nanosecond_order(self) -> None:
        project = init_project(self.root / "pets", "order-pet", "Order Pet", "fixture", "fixture", "fixture")
        target_root = self.root / "installed"
        self.make_package(target_root / "order-pet", "order-pet", b"current")
        backup_root = project / "backups" / "installed"
        older = backup_root / "z-uuid-sorts-last"
        newer = backup_root / "a-uuid-sorts-first"
        self.make_package(older, "order-pet", b"older", created_at_ns=100)
        self.make_package(newer, "order-pet", b"newer", created_at_ns=200)
        result = rollback_install(project, target_root)
        self.assertEqual(result["restored_backup"], str(newer.resolve()))
        self.assertEqual((target_root / "order-pet" / "spritesheet.webp").read_bytes(), b"newer")


if __name__ == "__main__":
    unittest.main()
