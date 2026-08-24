from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import petkit.build as build_module
import petkit.project as project_module
from PIL import Image
from petkit.build import (
    _preflight_edit_scope,
    _verify_build_artifact,
    _verify_edit_scope_for_acceptance,
    accept_build,
    authority_snapshot,
    build_project,
    install_build,
    look_basis_fingerprint,
    review_directions,
    rollback_install,
)
from petkit.contract import load_contract
from petkit.cli import cmd_import
from petkit.imageops import extract_row_strip
from petkit.project import (
    TransactionRecoveryError,
    approve_identity,
    create_variant,
    init_project,
    load_project,
    next_build_id,
    plan_edit,
    project_writer_lock,
    reconcile_pending_operations,
    save_project,
    sha256_file,
    upgrade_project,
)
from petkit.v2 import (
    CROSS_STATE_QUALITY_GATES,
    STANDARD_CONFUSION_PAIRS,
    STANDARD_FRAME_BEATS,
    STANDARD_FRAME_COUNTS,
    STANDARD_QUALITY_GATES,
    STANDARD_STATE_IDS,
)
from petkit.semantic import SEMANTIC_CONFUSION_PAIRS, SEMANTIC_STATE_OPTIONS
from tests.helpers import identity_image, replacement_frame, row_strip


class WorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.projects = self.root / "pets"
        self.contract = load_contract(2)
        self.project = init_project(
            self.projects,
            "test-moth",
            "Test Moth",
            "A synthetic moth used to verify the deterministic pipeline.",
            "A friendly rounded blue moth.",
            "flat test fixture",
        )
        approve_identity(self.project, identity_image(self.root / "identity.png"))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def prepare_design_gates(self) -> None:
        qa = self.project / "qa"
        qa.mkdir(parents=True, exist_ok=True)
        (qa / "standard-motion-plan.md").write_text("Synthetic motion plan with explicit beats and anti-confusion cues.\n", encoding="utf-8")
        (qa / "key-pose-concepts.png").write_bytes((self.root / "identity.png").read_bytes())
        (qa / "capability-audit.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "pass": True,
                    "states": [
                        {
                            "state": state,
                            "approved": True,
                            "capability": "body silhouette and planted limbs",
                            "thumbnail_cue": f"synthetic {state} silhouette",
                            "anti_confusion": "distinct fixture rhythm",
                        }
                        for state in STANDARD_STATE_IDS
                    ],
                }
            ),
            encoding="utf-8",
        )
        (qa / "key-pose-review.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "reviewer_id": "key-pose-fixture-01",
                    "reviewer_independent": True,
                    "pass": True,
                    "review_inputs": {
                        "full_size_seen": True,
                        "thumbnail_size_seen": True,
                        "prompts_or_motion_plan_seen": False,
                    },
                    "states": [
                        {
                            "state": state,
                            "full_read": True,
                            "thumbnail_read": True,
                            "note": "Synthetic fixture pose is readable at both sizes.",
                        }
                        for state in STANDARD_STATE_IDS
                    ],
                }
            ),
            encoding="utf-8",
        )

    def ingest(self, state_ids: list[str] | None = None) -> None:
        _, project = load_project(self.project)
        selected = state_ids or [state.id for state in self.contract.states]
        completed = set(project["generation"]["completed_states"])
        for state_id in selected:
            state = self.contract.state(state_id)
            strip = row_strip(self.project / "source" / "rows" / f"{state.id}.png", state, self.contract)
            state_dir = self.project / "source" / "frames" / state.id
            extract_row_strip(strip, state_dir, state, self.contract, "#00FF00", 60.0, "components")
            row_target = self.project / "source" / "rows" / state.id / "row-0001.png"
            row_target.parent.mkdir(parents=True, exist_ok=True)
            row_target.write_bytes(strip.read_bytes())
            project["generation"]["row_sources"][state.id] = {
                "path": row_target.relative_to(self.project).as_posix(),
                "sha256": sha256_file(row_target),
                "method": "synthetic-fixture",
            }
            completed.add(state.id)
        project["generation"]["completed_states"] = [state.id for state in self.contract.states if state.id in completed]
        project["status"] = "generating"
        if set(state.id for state in self.contract.states).issubset(completed):
            self.prepare_design_gates()
        if {"look-a", "look-b"}.issubset(completed):
            mechanics_path = self.project / "source" / "look-mechanics.json"
            mechanics_path.write_text(
                json.dumps(
                    {
                        "directions": [
                            {
                                "degrees": degrees,
                                "eye": f"synthetic eye cue {degrees}",
                                "head": f"synthetic head cue {degrees}",
                                "body": f"synthetic body cue {degrees}",
                            }
                            for degrees in self.contract.look_directions_degrees
                        ]
                    }
                ),
                encoding="utf-8",
            )
            cardinals_path = self.project / "source" / "cardinals" / "cardinals-fixture.png"
            cardinals_path.parent.mkdir(parents=True, exist_ok=True)
            cardinals_path.write_bytes((self.root / "identity.png").read_bytes())
            project["look"] = {
                "mechanics": {
                    "path": mechanics_path.relative_to(self.project).as_posix(),
                    "sha256": sha256_file(mechanics_path),
                },
                "cardinals": {
                    "approved": True,
                    "path": cardinals_path.relative_to(self.project).as_posix(),
                    "sha256": sha256_file(cardinals_path),
                },
                "row_9_approved": False,
                "row_9_approval": None,
            }
            basis_sha256 = look_basis_fingerprint(self.project, project)
            look_a = project["generation"]["row_sources"]["look-a"]
            project["look"]["row_9_approved"] = True
            project["look"]["row_9_approval"] = {
                "row_sha256": look_a["sha256"],
                "basis_sha256": basis_sha256,
                "review_note": "Synthetic coherent row fixture.",
            }
            project["generation"]["row_sources"]["look-b"]["row_9_basis_sha256"] = basis_sha256
        save_project(self.project, project)

    def review(
        self,
        build: dict[str, object],
        *,
        inherit_direction_from: str | None = None,
        reject_semantic: bool = False,
        reject_blind_independence: bool = False,
        duplicate_blind_reviewers: bool = False,
        duplicate_blind_submissions: bool = False,
        direction_reviewer_id: str = "direction-semantics-fixture",
    ) -> None:
        build_dir = Path(str(build["build_dir"]))
        build_record = json.loads((build_dir / "build.json").read_text(encoding="utf-8"))
        atlas_hash = sha256_file(build_dir / "spritesheet.webp")
        canonical_identity_sha256 = build_record["canonical_identity_sha256"]
        verdicts = []
        semantics = self.root / "semantics.json"
        if inherit_direction_from is None:
            answer = json.loads((build_dir / "qa-private" / "direction-blind-answer-key.json").read_text(encoding="utf-8"))
            for index in range(3):
                path = self.root / f"blind-{index}.json"
                path.write_text(
                    json.dumps(
                        {
                            "atlas_sha256": atlas_hash,
                            "canonical_identity_sha256": canonical_identity_sha256,
                            "reviewer_id": (
                                "blind-fixture-01"
                                if duplicate_blind_reviewers
                                else f"blind-fixture-{index + 1:02d}"
                            ),
                            "reviewer_independent": not reject_blind_independence,
                            "pairs": [
                                {
                                    "pair": pair["pair"],
                                    "A": pair["A"]["expected_direction"],
                                    "B": pair["B"]["expected_direction"],
                                }
                                for pair in answer["pairs"]
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                verdicts.append(path)
            if duplicate_blind_submissions:
                verdicts = [verdicts[0], verdicts[0], verdicts[0]]
            semantics.write_text(
                json.dumps(
                    {
                        "atlas_sha256": atlas_hash,
                        "canonical_identity_sha256": canonical_identity_sha256,
                        "reviewer_id": direction_reviewer_id,
                        "reviewer_independent": True,
                        "directions": [
                            {"degrees": degrees, "observed": f"fixture-{degrees}", "pass": True}
                            for degrees in self.contract.look_directions_degrees
                        ],
                    }
                ),
                encoding="utf-8",
            )
        else:
            semantics = None
        semantic_answer = json.loads((build_dir / "qa-private" / "semantic-recognition-answer-key.json").read_text(encoding="utf-8"))
        semantic = self.root / "semantic.json"
        semantic.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "atlas_sha256": atlas_hash,
                    "canonical_identity_sha256": canonical_identity_sha256,
                    "reviewer_id": "semantic-fixture-01",
                    "reviewer_independent": True,
                    "pass": True,
                    "note": "Synthetic anonymous semantic recognition passed at both sizes.",
                    "review_inputs": {
                        "canonical_identity_seen": True,
                        "semantic_full_sheet_seen": True,
                        "semantic_thumbnail_sheet_seen": True,
                        "semantic_full_previews_seen": True,
                        "semantic_thumbnail_previews_seen": True,
                        "calibration_controls_seen": True,
                        "prompts_or_motion_plan_seen": False,
                    },
                    "state_options": SEMANTIC_STATE_OPTIONS,
                    "assignments": [
                        {
                            "token": clip["token"],
                            "full_state": clip["state"],
                            "thumbnail_state": clip["state"],
                            "full_alternative": "none",
                            "thumbnail_alternative": "none",
                            "full_evidence": "Synthetic fixture has a distinct full-size action.",
                            "thumbnail_evidence": "Synthetic fixture remains distinct at UI size.",
                        }
                        for clip in semantic_answer["clips"]
                    ],
                    "pairwise_confusions": [
                        {
                            "states": list(pair),
                            "full_distinct": True,
                            "thumbnail_distinct": True,
                            "evidence": "Synthetic fixture uses distinct silhouette and rhythm cues.",
                        }
                        for pair in SEMANTIC_CONFUSION_PAIRS
                    ],
                    "calibration": [
                        {"id": control["id"], "result": "reject", "evidence": "Control is intentionally inert or ambiguous."}
                        for control in semantic_answer["controls"]
                    ],
                }
            ),
            encoding="utf-8",
        )
        visual = self.root / "visual.json"
        visual.write_text(
            json.dumps(
                {
                    "atlas_sha256": atlas_hash,
                    "canonical_identity_sha256": canonical_identity_sha256,
                    "reviewer_id": "visual-fixture-01",
                    "reviewer_independent": True,
                    "pass": True,
                    "note": "Synthetic V2 contact sheet and direction loop are coherent.",
                    "review_inputs": {
                        "canonical_identity_seen": True,
                        "normal_size_filmstrips_seen": True,
                        "animated_previews_seen": True,
                        "prompts_or_motion_plan_seen": False,
                    },
                    "standard_states": [
                        {
                            "state": state,
                            "observed_action": f"synthetic action for {state}",
                            "silhouette_signature": f"synthetic silhouette for {state}",
                            "frame_observations": [
                                {
                                    "index": index,
                                    "beat": STANDARD_FRAME_BEATS[state][index],
                                    "support": "fixture support/contact is coherent",
                                    "anatomy": "intact fixture",
                                    "contribution": "distinct fixture beat",
                                }
                                for index in range(STANDARD_FRAME_COUNTS[state])
                            ],
                            "transition_observations": [
                                {
                                    "from": index,
                                    "to": (index + 1) % STANDARD_FRAME_COUNTS[state],
                                    "plausible": True,
                                    "note": "continuous fixture transition",
                                }
                                for index in range(STANDARD_FRAME_COUNTS[state])
                            ],
                            "quality_gates": {
                                gate: {"pass": True, "note": f"fixture {gate} passed"}
                                for gate in STANDARD_QUALITY_GATES
                            },
                            "pass": True,
                            "note": "Fixture row is distinct and state-correct.",
                        }
                        for state in STANDARD_STATE_IDS
                    ],
                    "confusion_pairs": [
                        {
                            "states": list(pair),
                            "distinct": True,
                            "evidence": "Synthetic pair uses different silhouette and rhythm fixtures.",
                        }
                        for pair in STANDARD_CONFUSION_PAIRS
                    ],
                    "cross_state_consistency": {
                        gate: {"pass": True, "note": f"fixture cross-state {gate} passed"}
                        for gate in CROSS_STATE_QUALITY_GATES
                    },
                }
            ),
            encoding="utf-8",
        )
        visual_paths = []
        for index in range(3):
            path = self.root / f"visual-{index}.json"
            visual_payload = json.loads(visual.read_text(encoding="utf-8"))
            visual_payload["reviewer_id"] = f"visual-fixture-{index + 1:02d}"
            path.write_text(json.dumps(visual_payload), encoding="utf-8")
            visual_paths.append(path)
        semantic_paths = []
        for index in range(3):
            path = self.root / f"semantic-{index}.json"
            semantic_payload = json.loads(semantic.read_text(encoding="utf-8"))
            semantic_payload["reviewer_id"] = f"semantic-fixture-{index + 1:02d}"
            if reject_semantic and index == 0:
                semantic_payload["pass"] = False
            path.write_text(json.dumps(semantic_payload), encoding="utf-8")
            semantic_paths.append(path)
        review_directions(
            self.project,
            str(build["build_id"]),
            direction_semantics=semantics,
            blind_verdicts=verdicts,
            semantic_verdicts=semantic_paths,
            independent_visual_qas=visual_paths,
            continuity_override_note="Synthetic geometry intentionally triggers continuity warnings.",
            inherit_direction_from=inherit_direction_from,
        )

    def test_partial_work_is_resumable(self) -> None:
        self.ingest(["idle", "running-right"])
        _, project = load_project(self.project)
        self.assertEqual(project["generation"]["completed_states"], ["idle", "running-right"])
        self.assertTrue((self.project / "source" / "frames" / "idle" / "00.png").is_file())
        png_frame = self.project / "source" / "frames" / "idle" / "00.png"
        webp_frame = png_frame.with_suffix(".webp")
        png_frame.rename(webp_frame)
        status = subprocess.run(
            [sys.executable, "-m", "petkit", "status", "--project", str(self.project)],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(json.loads(status.stdout)["states"]["idle"]["frame_count"], 6)
        webp_frame.rename(png_frame)
        self.ingest()
        _, resumed = load_project(self.project)
        self.assertEqual(len(resumed["generation"]["completed_states"]), 11)

    def test_status_and_build_share_design_gate_preflight(self) -> None:
        self.ingest()
        (self.project / "qa" / "key-pose-review.json").unlink()
        status = subprocess.run(
            [sys.executable, "-m", "petkit", "status", "--project", str(self.project)],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
            text=True,
        )
        status_payload = json.loads(status.stdout)
        self.assertFalse(status_payload["ready_to_build"])
        self.assertTrue(
            any(
                blocker["phase"] == "build" and "key-pose-review.json" in blocker["message"]
                for blocker in status_payload["blockers"]
            )
        )
        with patch("petkit.build.assemble_v2") as assemble:
            with self.assertRaisesRegex(ValueError, "key-pose-review.json"):
                build_project(self.project)
            assemble.assert_not_called()
        self.assertFalse((self.project / "builds" / "build-0001").exists())

    def test_build_rejects_source_mutation_during_the_build_window(self) -> None:
        self.ingest()
        source = self.project / "source" / "frames" / "idle" / "00.png"
        original_compose = build_module.compose_atlas
        mutated = False

        def mutate_then_compose(*args: object, **kwargs: object) -> object:
            nonlocal mutated
            if not mutated:
                replacement_frame(source, self.contract, (250, 40, 90, 255))
                mutated = True
            return original_compose(*args, **kwargs)

        with patch("petkit.build.compose_atlas", side_effect=mutate_then_compose):
            with self.assertRaisesRegex(ValueError, "source inputs changed during build"):
                build_project(self.project)
        _, project = load_project(self.project)
        self.assertIsNone(project["current_build"])
        self.assertFalse((self.project / "builds" / "build-0001").exists())
        self.assertEqual(list((self.project / "builds").glob(".*.staging-*")), [])

    def test_build_rejects_transient_source_bytes_copied_after_preflight(self) -> None:
        self.ingest()
        source = self.project / "source" / "frames" / "idle" / "00.png"
        original_bytes = source.read_bytes()
        changed_path = replacement_frame(self.root / "transient.png", self.contract, (250, 40, 90, 255))
        changed_bytes = changed_path.read_bytes()
        real_copy = shutil.copy2
        injected = False

        def copy_with_aba(source_value: str | Path, target_value: str | Path, *args: object, **kwargs: object) -> object:
            nonlocal injected
            source_path = Path(source_value)
            if source_path == source and not injected:
                injected = True
                source.write_bytes(changed_bytes)
                try:
                    return real_copy(source, target_value, *args, **kwargs)
                finally:
                    source.write_bytes(original_bytes)
            return real_copy(source_value, target_value, *args, **kwargs)

        with patch("petkit.build.shutil.copy2", side_effect=copy_with_aba):
            with self.assertRaisesRegex(ValueError, "source inputs changed while snapshotting"):
                build_project(self.project)
        self.assertTrue(injected)
        self.assertEqual(source.read_bytes(), original_bytes)
        self.assertFalse((self.project / "builds" / "build-0001").exists())

    def test_build_cancellation_reconciles_every_precommit_publication_phase(self) -> None:
        self.ingest()
        final = self.project / "builds" / "build-0001"
        real_replace = build_module.os.replace
        phases = ("after-input-mkdir", "before-assembly", "during-work", "before-publish", "after-publish", "before-save")

        for phase in phases:
            with self.subTest(phase=phase):
                snapshots: list[Path] = []

                def capture_trace(event: str, **details: object) -> None:
                    if event == "build.after-input-mkdir":
                        snapshots.append(Path(str(details["path"])))
                        if phase == "after-input-mkdir":
                            raise KeyboardInterrupt("injected after-input-mkdir")

                def interrupt_publish(source: str | Path, destination: str | Path) -> None:
                    source_path = Path(source)
                    destination_path = Path(destination)
                    if destination_path == final and source_path.name.startswith(".build-0001.staging-"):
                        if phase == "after-publish":
                            real_replace(source, destination)
                        raise KeyboardInterrupt(f"injected {phase}")
                    real_replace(source, destination)

                with ExitStack() as stack:
                    stack.enter_context(patch("petkit.build.transaction_trace", side_effect=capture_trace))
                    if phase == "after-input-mkdir":
                        pass
                    elif phase == "before-assembly":
                        stack.enter_context(
                            patch("petkit.build.compose_atlas", side_effect=KeyboardInterrupt("injected before-assembly"))
                        )
                    elif phase == "during-work":
                        stack.enter_context(
                            patch("petkit.build.make_contact_sheet", side_effect=KeyboardInterrupt("injected during-work"))
                        )
                    elif phase in {"before-publish", "after-publish"}:
                        stack.enter_context(patch("petkit.build.os.replace", side_effect=interrupt_publish))
                    else:
                        stack.enter_context(
                            patch("petkit.build.save_project", side_effect=KeyboardInterrupt("injected before-save"))
                        )
                    with self.assertRaises(KeyboardInterrupt) as raised:
                        build_project(self.project)

                self.assertEqual(str(raised.exception), f"injected {phase}")
                _, durable = load_project(self.project)
                self.assertIsNone(durable["current_build"])
                self.assertFalse(final.exists())
                self.assertEqual(list((self.project / "builds").glob(".build-0001*")), [])
                self.assertTrue(snapshots)
                self.assertTrue(all(not path.exists() for path in snapshots))
                with project_writer_lock(self.project):
                    pass

    def test_build_cancellation_after_durable_pointer_preserves_verified_final(self) -> None:
        self.ingest()
        real_save = build_module.save_project
        snapshots: list[Path] = []

        def capture_trace(event: str, **details: object) -> None:
            if event == "build.after-input-mkdir":
                snapshots.append(Path(str(details["path"])))

        def save_then_interrupt(*args: object, **kwargs: object) -> None:
            real_save(*args, **kwargs)
            raise KeyboardInterrupt("injected after-save")

        with (
            patch("petkit.build.transaction_trace", side_effect=capture_trace),
            patch("petkit.build.save_project", side_effect=save_then_interrupt),
        ):
            with self.assertRaises(KeyboardInterrupt) as raised:
                build_project(self.project)

        _, durable = load_project(self.project)
        self.assertEqual(durable["current_build"], "build-0001")
        final = self.project / "builds" / "build-0001"
        self.assertTrue(final.is_dir())
        _verify_build_artifact(self.project, durable, "build-0001")
        self.assertTrue(any("durably committed" in note for note in raised.exception.__notes__))
        self.assertEqual(list((self.project / "builds").glob(".build-0001*")), [])
        self.assertTrue(all(not path.exists() for path in snapshots))
        with project_writer_lock(self.project):
            pass

    def test_candidate_cancellation_uses_durable_record_or_event_and_never_reuses_history(self) -> None:
        self.ingest()

        def interrupt_unrecorded_publish(event: str, **_details: object) -> None:
            if event == "build.after-publish":
                raise KeyboardInterrupt("injected candidate before authority")

        with patch("petkit.build.transaction_trace", side_effect=interrupt_unrecorded_publish):
            with self.assertRaises(KeyboardInterrupt):
                build_project(self.project, draft=True)
        self.assertFalse((self.project / "builds" / "build-0001").exists())
        self.assertFalse((self.project / "history" / "candidate-build-0001.json").exists())
        self.assertEqual(next_build_id(self.project), "build-0001")

        candidate_record_path = self.project / "history" / "candidate-build-0001.json"

        def interrupt_after_candidate_record(event: str, **details: object) -> None:
            if event == "json.after-replace" and details.get("path") == str(candidate_record_path):
                raise KeyboardInterrupt("injected after candidate record")

        with patch("petkit.project.transaction_trace", side_effect=interrupt_after_candidate_record):
            with self.assertRaises(KeyboardInterrupt) as recorded:
                build_project(self.project, draft=True)
        self.assertTrue((self.project / "builds" / "build-0001").is_dir())
        self.assertTrue(candidate_record_path.is_file())
        self.assertTrue(any("durably committed" in note for note in recorded.exception.__notes__))
        self.assertEqual(next_build_id(self.project), "build-0002")

        def interrupt_after_candidate_event(event: str, **_details: object) -> None:
            if event == "build.after-candidate-event":
                raise SystemExit("injected after candidate event")

        with patch("petkit.build.transaction_trace", side_effect=interrupt_after_candidate_event):
            with self.assertRaises(SystemExit) as event_committed:
                build_project(self.project, draft=True)
        self.assertTrue((self.project / "builds" / "build-0002").is_dir())
        self.assertTrue((self.project / "history" / "candidate-build-0002.json").is_file())
        events = (self.project / "history" / "events.jsonl").read_text(encoding="utf-8")
        self.assertIn('"build_id": "build-0002"', events)
        self.assertTrue(any("durably committed" in note for note in event_committed.exception.__notes__))
        self.assertEqual(next_build_id(self.project), "build-0003")
        _, durable = load_project(self.project)
        self.assertIsNone(durable["current_build"])
        self.assertEqual(list((self.project / ".petkit-recovery").glob("*.json")), [])

    def test_build_cancellation_aggregates_unrecovered_cleanup_paths(self) -> None:
        self.ingest()
        real_rmtree = shutil.rmtree
        real_replace = project_module.os.replace
        private_paths: list[Path] = []
        quarantines: list[Path] = []

        def capture_trace(event: str, **details: object) -> None:
            if event == "build.after-input-mkdir":
                private_paths.append(Path(str(details["path"])))

        def fail_cleanup(path: str | Path, *args: object, **kwargs: object) -> None:
            candidate = Path(path)
            if ".build-0001.staging-" in candidate.name or "petkit-build-0001-" in candidate.name:
                raise OSError(f"injected cleanup failure at {candidate}")
            real_rmtree(path, *args, **kwargs)

        def capture_quarantine(source: str | Path, destination: str | Path) -> None:
            target = Path(destination)
            if target.name.endswith(".recovery") and (
                ".build-0001.staging-" in target.name or "petkit-build-0001-" in target.name
            ):
                quarantines.append(target)
            real_replace(source, destination)

        try:
            with (
                patch("petkit.build.transaction_trace", side_effect=capture_trace),
                patch("petkit.build.compose_atlas", side_effect=KeyboardInterrupt("injected cancelled build")),
                patch("petkit.project.shutil.rmtree", side_effect=fail_cleanup),
                patch("petkit.project.os.replace", side_effect=capture_quarantine),
            ):
                with self.assertRaises(TransactionRecoveryError) as raised:
                    build_project(self.project)
            self.assertIsInstance(raised.exception.__cause__, KeyboardInterrupt)
            message = str(raised.exception)
            self.assertIn("interrupted build staging directory", message)
            self.assertIn("private build input snapshot", message)
            self.assertGreaterEqual(len(quarantines), 2)
            for quarantine in quarantines:
                self.assertTrue(quarantine.exists())
                self.assertIn(str(quarantine), message)
            self.assertFalse((self.project / "builds" / "build-0001").exists())
            _, durable = load_project(self.project)
            self.assertIsNone(durable["current_build"])
            with project_writer_lock(self.project):
                reconcile_pending_operations(self.project)
            self.assertTrue(all(not quarantine.exists() for quarantine in quarantines))
            self.assertEqual(list((self.project / ".petkit-recovery").glob("*.json")), [])
        finally:
            for quarantine in dict.fromkeys(quarantines):
                if quarantine.exists():
                    real_rmtree(quarantine)
            for private in private_paths:
                if private.exists():
                    real_rmtree(private)

    def test_changed_despill_cache_is_not_reused(self) -> None:
        self.ingest()
        first = build_project(self.project)
        first_dir = Path(str(first["build_dir"]))
        first_record = json.loads((first_dir / "build.json").read_text(encoding="utf-8"))
        cache = first_dir / "spritesheet.png"
        original_cache = cache.read_bytes()
        with Image.open(cache) as opened:
            changed = opened.convert("RGBA")
        changed.putpixel((0, 0), (255, 0, 255, 255))
        changed_path = self.root / "changed-cache.png"
        changed.save(changed_path)
        changed_bytes = changed_path.read_bytes()
        real_copy = shutil.copy2
        injected = False

        def copy_with_aba(source_value: str | Path, target_value: str | Path, *args: object, **kwargs: object) -> object:
            nonlocal injected
            source_path = Path(source_value)
            if source_path == cache and not injected:
                injected = True
                cache.write_bytes(changed_bytes)
                try:
                    return real_copy(cache, target_value, *args, **kwargs)
                finally:
                    cache.write_bytes(original_cache)
            return real_copy(source_value, target_value, *args, **kwargs)

        with patch("petkit.build.shutil.copy2", side_effect=copy_with_aba):
            second = build_project(self.project)
        second_record = json.loads(
            (Path(str(second["build_dir"])) / "build.json").read_text(encoding="utf-8")
        )
        self.assertTrue(injected)
        self.assertEqual(cache.read_bytes(), original_cache)
        self.assertIsNone(second_record["artifact_reuse"]["parent_build"])
        self.assertEqual(second_record["artifact_reuse"]["preview_states"], [])
        self.assertEqual(second_record["source_sha256"], first_record["source_sha256"])

    def test_parent_artifacts_are_privately_snapshotted_before_reuse_and_comparison(self) -> None:
        self.ingest()
        first = build_project(self.project)
        first_dir = Path(str(first["build_dir"]))
        first_record = json.loads((first_dir / "build.json").read_text(encoding="utf-8"))
        preview = first_dir / "previews" / "idle.gif"
        filmstrip = first_dir / "qa" / "standard-filmstrips" / "idle.png"
        parent_contact = first_dir / "contact-sheet.png"
        parent_contact.write_bytes(b"corrupt live contact sheet must not be consumed")
        original_preview = preview.read_bytes()
        original_filmstrip = filmstrip.read_bytes()
        real_copy = shutil.copy2
        real_before_after = build_module.make_before_after_sheet
        injected: set[Path] = set()
        before_inputs: list[Path] = []

        def copy_with_aba(source_value: str | Path, target_value: str | Path, *args: object, **kwargs: object) -> object:
            source_path = Path(source_value)
            originals = {preview: original_preview, filmstrip: original_filmstrip}
            if source_path in originals and source_path not in injected:
                injected.add(source_path)
                source_path.write_bytes(b"transient parent artifact mutation")
                try:
                    return real_copy(source_path, target_value, *args, **kwargs)
                finally:
                    source_path.write_bytes(originals[source_path])
            return real_copy(source_value, target_value, *args, **kwargs)

        def record_before_after(before: Path, after: Path, output: Path) -> None:
            before_inputs.append(Path(before))
            real_before_after(before, after, output)

        with (
            patch("petkit.build.shutil.copy2", side_effect=copy_with_aba),
            patch("petkit.build.make_before_after_sheet", side_effect=record_before_after),
        ):
            second = build_project(self.project)
        second_dir = Path(str(second["build_dir"]))
        second_record = json.loads((second_dir / "build.json").read_text(encoding="utf-8"))
        self.assertEqual(injected, {preview, filmstrip})
        self.assertEqual(preview.read_bytes(), original_preview)
        self.assertEqual(filmstrip.read_bytes(), original_filmstrip)
        self.assertEqual(second_record["artifact_reuse"]["preview_states"], [])
        self.assertEqual(second_record["artifact_reuse"]["standard_filmstrips"], [])
        self.assertEqual(
            second_record["artifact_sha256"]["previews"]["idle.gif"],
            first_record["artifact_sha256"]["previews"]["idle.gif"],
        )
        self.assertEqual(len(before_inputs), 1)
        self.assertNotEqual(before_inputs[0], parent_contact)
        self.assertNotIn(first_dir, before_inputs[0].parents)

        atlas = second_dir / "spritesheet.webp"
        original_atlas = atlas.read_bytes()
        with Image.open(atlas) as opened:
            corrupt_atlas = opened.convert("RGBA")
        corrupt_atlas.putpixel((0, 0), (255, 0, 255, 255))
        corrupt_atlas.save(atlas, format="WEBP", lossless=True)
        try:
            with self.assertRaisesRegex(ValueError, "parent atlas no longer matches"):
                build_project(self.project)
        finally:
            atlas.write_bytes(original_atlas)
        self.assertFalse((self.project / "builds" / "build-0003").exists())

    def test_standard_only_parent_atlas_reaches_the_v2_upgrade_comparison(self) -> None:
        self.ingest()
        first = build_project(self.project)
        first_dir = Path(str(first["build_dir"]))
        atlas = first_dir / "spritesheet.webp"
        with Image.open(atlas) as opened:
            standard_only = opened.convert("RGBA").crop(
                (0, 0, self.contract.width, self.contract.standard_rows * self.contract.cell_height)
            )
        standard_only.save(atlas, format="WEBP", lossless=True)
        first_record_path = first_dir / "build.json"
        first_record = json.loads(first_record_path.read_text(encoding="utf-8"))
        first_record["spritesheet_sha256"] = sha256_file(atlas)
        first_record_path.write_text(json.dumps(first_record), encoding="utf-8")

        upgraded = build_project(self.project)

        upgraded_dir = Path(str(upgraded["build_dir"]))
        change_report = json.loads((upgraded_dir / "change-report.json").read_text(encoding="utf-8"))
        self.assertEqual(change_report["added_states"], ["look-a", "look-b"])
        self.assertTrue(change_report["v2_upgrade"]["alpha_geometry_preserved"])
        self.assertTrue((upgraded_dir / "before-after.png").is_file())

    def test_build_publication_rolls_back_authority_failure_and_warns_after_commit(self) -> None:
        self.ingest()
        real_rmtree = shutil.rmtree
        failed_publications: list[Path] = []

        def fail_quarantined_build_cleanup(path: str | Path, *args: object, **kwargs: object) -> None:
            candidate = Path(path)
            if ".failed-publication-" in candidate.name:
                failed_publications.append(candidate)
                raise OSError("injected quarantined build cleanup failure")
            real_rmtree(path, *args, **kwargs)

        with (
            patch("petkit.build.save_project", side_effect=OSError("injected metadata commit failure")),
            patch("petkit.build.shutil.rmtree", side_effect=fail_quarantined_build_cleanup),
        ):
            with self.assertRaisesRegex(RuntimeError, "cleanup was incomplete"):
                build_project(self.project)
        _, failed = load_project(self.project)
        self.assertIsNone(failed["current_build"])
        self.assertFalse((self.project / "builds" / "build-0001").exists())
        self.assertEqual(list((self.project / "builds").glob(".*.staging-*")), [])
        self.assertEqual(len(failed_publications), 1)
        self.assertTrue(failed_publications[0].exists())
        orphan = self.project / "builds" / "build-0001"
        failed_publications[0].replace(orphan)
        with self.assertRaisesRegex(ValueError, "current release build"):
            review_directions(
                self.project,
                "build-0001",
                direction_semantics=None,
                blind_verdicts=[],
                semantic_verdicts=[],
                independent_visual_qas=[],
            )
        self.assertFalse(orphan.exists())

        with patch("petkit.build.append_event", side_effect=OSError("injected event failure")):
            built = build_project(self.project)
        self.assertEqual(built["build_id"], "build-0001")
        self.assertEqual(len(built["post_commit_warnings"]), 1)
        self.assertIn("pointer were committed", built["post_commit_warnings"][0])
        _, committed = load_project(self.project)
        self.assertEqual(committed["current_build"], "build-0001")

    def test_private_build_snapshot_cleanup_failure_is_quarantined_before_publication(self) -> None:
        self.ingest()
        real_rmtree = shutil.rmtree
        real_replace = build_module.os.replace
        quarantines: list[Path] = []

        def fail_private_snapshot_cleanup(path: str | Path, *args: object, **kwargs: object) -> None:
            candidate = Path(path)
            if "petkit-build-0001-" in candidate.name:
                raise OSError("injected private snapshot cleanup failure")
            real_rmtree(path, *args, **kwargs)

        def capture_quarantine(source: str | Path, destination: str | Path) -> None:
            target = Path(destination)
            if "petkit-build-0001-" in target.name and target.name.endswith(".recovery"):
                quarantines.append(target)
            real_replace(source, destination)

        with (
            patch("petkit.build.shutil.rmtree", side_effect=fail_private_snapshot_cleanup),
            patch("petkit.build.os.replace", side_effect=capture_quarantine),
        ):
            with self.assertRaisesRegex(TransactionRecoveryError, "private build input snapshot"):
                build_project(self.project)
        _, failed = load_project(self.project)
        self.assertIsNone(failed["current_build"])
        self.assertFalse((self.project / "builds" / "build-0001").exists())
        self.assertEqual(list((self.project / "builds").glob(".*.staging-*")), [])
        self.assertEqual(len(quarantines), 1)
        self.assertTrue(quarantines[0].exists())
        with project_writer_lock(self.project):
            reconcile_pending_operations(self.project)
        self.assertFalse(quarantines[0].exists())

    def test_build_surfaces_staging_and_private_snapshot_cleanup_failures_together(self) -> None:
        self.ingest()
        real_rmtree = shutil.rmtree
        real_replace = build_module.os.replace
        leaked_paths: list[Path] = []

        def fail_both_cleanups(path: str | Path, *args: object, **kwargs: object) -> None:
            candidate = Path(path)
            if ".build-0001.staging-" in candidate.name:
                leaked_paths.append(candidate)
                raise OSError("injected staging cleanup failure")
            if "petkit-build-0001-" in candidate.name:
                leaked_paths.append(candidate)
                raise OSError("injected private snapshot cleanup failure")
            real_rmtree(path, *args, **kwargs)

        def fail_private_quarantine(source: str | Path, destination: str | Path) -> None:
            target = Path(destination)
            if "petkit-build-0001-" in target.name and target.name.endswith(".recovery"):
                raise OSError("injected private quarantine failure")
            real_replace(source, destination)

        try:
            with (
                patch("petkit.build.assemble_v2", side_effect=OSError("injected build operation failure")),
                patch("petkit.build.shutil.rmtree", side_effect=fail_both_cleanups),
                patch("petkit.build.os.replace", side_effect=fail_private_quarantine),
            ):
                with self.assertRaises(RuntimeError) as raised:
                    build_project(self.project)
            message = str(raised.exception)
            self.assertIn("injected build operation failure", message)
            self.assertIn("injected staging cleanup failure", message)
            self.assertIn("injected private snapshot cleanup failure", message)
            self.assertIn("injected private quarantine failure", message)
            for leaked in leaked_paths:
                if leaked.exists():
                    self.assertIn(str(leaked), message)
            _, failed = load_project(self.project)
            self.assertIsNone(failed["current_build"])
            self.assertFalse((self.project / "builds" / "build-0001").exists())
        finally:
            for leaked in dict.fromkeys(leaked_paths):
                if leaked.exists():
                    real_rmtree(leaked)

    def test_variant_rejects_live_authority_not_present_in_the_accepted_release(self) -> None:
        self.ingest()
        baseline = build_project(self.project)
        self.review(baseline)
        accept_build(
            self.project,
            baseline["build_id"],
            confirm_visual_qa=True,
            review_note="Synthetic baseline accepted before the authority-mutation regression.",
        )
        valid_child = create_variant(
            self.project,
            self.projects,
            "verified-authority-child",
            "Verified Authority Child",
        )
        _, child_metadata = load_project(valid_child)
        baseline_record = json.loads(
            (Path(str(baseline["build_dir"])) / "build.json").read_text(encoding="utf-8")
        )
        fork_snapshot = child_metadata["generation"]["fork_snapshot"]
        self.assertEqual(fork_snapshot["parent_build"], baseline["build_id"])
        self.assertEqual(fork_snapshot["accepted_source_sha256"], baseline_record["source_sha256"])
        self.assertEqual(fork_snapshot["accepted_build_inputs"], baseline_record["build_inputs"])

        source_frame = self.project / "source" / "frames" / "idle" / "00.png"
        original_source = source_frame.read_bytes()
        replacement_frame(source_frame, self.contract, (245, 50, 105, 255))
        with self.assertRaisesRegex(ValueError, "source inputs no longer match"):
            create_variant(
                self.project,
                self.projects,
                "drifted-source-child",
                "Drifted Source Child",
            )
        self.assertFalse((self.projects / "drifted-source-child").exists())
        source_frame.write_bytes(original_source)

        _, accepted = load_project(self.project)
        canonical = self.project / accepted["identity"]["canonical_reference"]
        original_canonical = canonical.read_bytes()
        canonical.write_bytes(identity_image(self.root / "changed-canonical.png", color=(210, 55, 120)).read_bytes())
        with self.assertRaisesRegex(ValueError, "canonical identity"):
            create_variant(
                self.project,
                self.projects,
                "drifted-canonical-child",
                "Drifted Canonical Child",
            )
        self.assertFalse((self.projects / "drifted-canonical-child").exists())
        canonical.write_bytes(original_canonical)

        mechanics = json.loads(
            (self.project / accepted["look"]["mechanics"]["path"]).read_text(encoding="utf-8")
        )
        mechanics["directions"][0]["eye"] = "changed supported mechanics cue"
        (self.project / accepted["look"]["mechanics"]["path"]).write_text(
            json.dumps(mechanics),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "look mechanics no longer matches"):
            create_variant(
                self.project,
                self.projects,
                "unaccepted-authority-child",
                "Unaccepted Authority Child",
            )
        self.assertFalse((self.projects / "unaccepted-authority-child").exists())

    def test_variant_cancellation_removes_private_staging_and_published_child_state(self) -> None:
        self.ingest()
        baseline = build_project(self.project)
        self.review(baseline)
        accept_build(
            self.project,
            baseline["build_id"],
            confirm_visual_qa=True,
            review_note="Synthetic accepted parent for cancellation-safe variant publication.",
        )
        real_replace = project_module.os.replace
        real_unlink = Path.unlink
        phases = ("parent-copy", "child-copy", "before-publish", "after-publish", "after-marker-remove")

        for phase in phases:
            with self.subTest(phase=phase):
                variant_id = f"cancel-{phase}"
                destination = self.projects / variant_id
                snapshots: list[Path] = []

                def capture_trace(event: str, **details: object) -> None:
                    if event == "variant-parent.after-snapshot-mkdir":
                        snapshots.append(Path(str(details["path"])))

                def interrupt_publish(source: str | Path, target: str | Path) -> None:
                    target_path = Path(target)
                    if target_path.name == variant_id:
                        if phase == "after-publish":
                            real_replace(source, target)
                        raise SystemExit(f"injected {phase}")
                    real_replace(source, target)

                def interrupt_marker_unlink(path: Path, *args: object, **kwargs: object) -> None:
                    candidate = Path(path)
                    real_unlink(candidate, *args, **kwargs)
                    if candidate.name == ".petkit-variant-operation.json":
                        raise SystemExit("injected after-marker-remove")

                with ExitStack() as stack:
                    stack.enter_context(patch("petkit.build.transaction_trace", side_effect=capture_trace))
                    if phase == "parent-copy":
                        stack.enter_context(
                            patch(
                                "petkit.build.copy_tree_without_symlinks",
                                side_effect=SystemExit("injected parent-copy"),
                            )
                        )
                    elif phase == "child-copy":
                        stack.enter_context(
                            patch(
                                "petkit.project.copy_tree_without_symlinks",
                                side_effect=SystemExit("injected child-copy"),
                            )
                        )
                    elif phase in {"before-publish", "after-publish"}:
                        stack.enter_context(patch("petkit.project.os.replace", side_effect=interrupt_publish))
                    else:
                        stack.enter_context(
                            patch(
                                "petkit.project.Path.unlink",
                                autospec=True,
                                side_effect=interrupt_marker_unlink,
                            )
                        )
                    with self.assertRaises(SystemExit) as raised:
                        create_variant(self.project, self.projects, variant_id, f"Cancel {phase}")

                self.assertEqual(str(raised.exception), f"injected {phase}")
                committed = phase in {"after-publish", "after-marker-remove"}
                self.assertEqual(destination.exists(), committed)
                if committed:
                    _, child = load_project(destination)
                    self.assertEqual(child["parent_id"], "test-moth")
                    self.assertFalse((destination / ".petkit-variant-operation.json").exists())
                    self.assertTrue(any("durably published" in note for note in raised.exception.__notes__))
                self.assertEqual(list(self.projects.glob(f".{variant_id}.variant-*")), [])
                self.assertEqual(list(self.projects.glob(f".{variant_id}.cancelled-*")), [])
                self.assertTrue(snapshots)
                self.assertTrue(all(not path.exists() for path in snapshots))
                self.assertEqual(list((self.project / ".petkit-recovery").glob("*.json")), [])
                with project_writer_lock(self.project):
                    pass

    def test_variant_cancellation_aggregates_snapshot_and_child_cleanup_failures(self) -> None:
        self.ingest()
        baseline = build_project(self.project)
        self.review(baseline)
        accept_build(
            self.project,
            baseline["build_id"],
            confirm_visual_qa=True,
            review_note="Synthetic accepted parent for cancellation cleanup aggregation.",
        )
        variant_id = "cancel-cleanup"
        destination = self.projects / variant_id
        real_rmtree = shutil.rmtree
        real_replace = project_module.os.replace
        quarantines: list[Path] = []

        def fail_operation_cleanup(path: str | Path, *args: object, **kwargs: object) -> None:
            candidate = Path(path)
            if "petkit-variant-build-0001-" in candidate.name or f".{variant_id}.variant-" in candidate.name:
                raise OSError(f"injected variant cleanup failure at {candidate}")
            real_rmtree(path, *args, **kwargs)

        def capture_quarantine(source: str | Path, target: str | Path) -> None:
            destination_path = Path(target)
            if destination_path.name.endswith((".recovery", ".cleanup")):
                quarantines.append(destination_path)
            real_replace(source, target)

        try:
            with (
                patch(
                    "petkit.project.copy_tree_without_symlinks",
                    side_effect=SystemExit("injected variant child cancellation"),
                ),
                patch("petkit.project.shutil.rmtree", side_effect=fail_operation_cleanup),
                patch("petkit.project.os.replace", side_effect=capture_quarantine),
            ):
                with self.assertRaises(TransactionRecoveryError) as raised:
                    create_variant(self.project, self.projects, variant_id, "Cancel Cleanup")
            message = str(raised.exception)
            self.assertIn("SystemExit: injected variant child cancellation", message)
            self.assertIn("private variant parent snapshot", message)
            self.assertIn("interrupted variant staging project", message)
            self.assertGreaterEqual(len(quarantines), 2)
            for quarantine in quarantines:
                self.assertTrue(quarantine.exists())
                self.assertIn(str(quarantine), message)
            self.assertFalse(destination.exists())
            with project_writer_lock(self.project):
                reconcile_pending_operations(self.project)
            self.assertTrue(all(not quarantine.exists() for quarantine in quarantines))
            self.assertEqual(list((self.project / ".petkit-recovery").glob("*.json")), [])
        finally:
            for quarantine in dict.fromkeys(quarantines):
                if quarantine.exists():
                    real_rmtree(quarantine)

    def test_look_approval_fingerprints_reject_stale_upstream_authority(self) -> None:
        self.ingest()
        _, project = load_project(self.project)
        mechanics_path = self.project / project["look"]["mechanics"]["path"]
        mechanics_path.write_text(
            mechanics_path.read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )
        project["look"]["mechanics"]["sha256"] = sha256_file(mechanics_path)
        save_project(self.project, project)

        with self.assertRaisesRegex(ValueError, "row 9 approval is stale"):
            authority_snapshot(self.project, project)

        refreshed_basis = look_basis_fingerprint(self.project, project)
        project["look"]["row_9_approval"]["basis_sha256"] = refreshed_basis
        save_project(self.project, project)
        with self.assertRaisesRegex(ValueError, "look-b source row is stale"):
            authority_snapshot(self.project, project)

        project["generation"]["row_sources"]["look-b"]["row_9_basis_sha256"] = refreshed_basis
        save_project(self.project, project)
        snapshot = authority_snapshot(self.project, project)
        self.assertEqual(snapshot["mechanics_sha256"], sha256_file(mechanics_path))
        self.assertEqual(snapshot["row_9_basis_sha256"], refreshed_basis)

    def test_replanned_edit_keeps_accepted_authority_and_compares_with_current_build(self) -> None:
        _, project = load_project(self.project)
        for build_id, source_hash in (("build-0001", "1" * 64), ("build-0002", "2" * 64)):
            build_dir = self.project / "builds" / build_id
            build_dir.mkdir()
            (build_dir / "build.json").write_text(
                json.dumps(
                    {
                        "build_id": build_id,
                        "pet_id": project["id"],
                        "source_sha256": {"idle": {"00.png": source_hash}},
                        "build_inputs": {"authority_fingerprint": "a" * 64},
                    }
                ),
                encoding="utf-8",
            )
        project["accepted_build"] = "build-0001"
        project["current_build"] = "build-0002"
        save_project(self.project, project)

        edit = plan_edit(
            self.project,
            "deterministic",
            "Continue from a failed release without losing accepted authority.",
            ["idle"],
        )
        self.assertEqual(edit["initial_baseline_build"], "build-0001")
        self.assertEqual(edit["comparison_build"], "build-0002")
        self.assertEqual(edit["baseline_source_sha256"], {"idle": {"00.png": "1" * 64}})
        _, replanned_project = load_project(self.project)
        with self.assertRaisesRegex(ValueError, "divergent edit"):
            _verify_edit_scope_for_acceptance(replanned_project, "build-0001", {})

    def test_look_b_only_authority_change_stays_in_look_b_scope(self) -> None:
        authority = {
            "canonical_identity_sha256": "1" * 64,
            "mechanics_sha256": "2" * 64,
            "cardinals_sha256": "3" * 64,
            "look_a_sha256": "4" * 64,
            "look_b_sha256": "5" * 64,
            "row_9_basis_sha256": "6" * 64,
        }
        baseline_inputs = {
            "chroma_key": "#00FF00",
            "chroma_threshold": 96.0,
            "authority": authority,
        }
        project = {
            "accepted_build": "build-0001",
            "active_edit": {
                "comparison_build": "build-0001",
                "allowed_states": ["look-b"],
                "baseline_source_sha256": {},
                "baseline_build_inputs": baseline_inputs,
            },
        }
        current_authority = {**authority, "look_b_sha256": "7" * 64}
        changed = _preflight_edit_scope(
            project,
            self.root / "build-0001",
            {"source_sha256": {}, "build_inputs": baseline_inputs},
            {},
            {**baseline_inputs, "authority": current_authority},
            self.contract,
        )
        self.assertEqual(changed, {"look-b"})

    def test_v1_upgrade_requires_a_fresh_v2_accepted_baseline(self) -> None:
        metadata_path = self.project / "pet-project.json"
        project = json.loads(metadata_path.read_text(encoding="utf-8"))
        project["contract_version"] = 1
        project["current_build"] = "build-0001"
        project["accepted_build"] = "build-0001"
        metadata_path.write_text(json.dumps(project), encoding="utf-8")

        result = upgrade_project(self.project)
        self.assertEqual(result["from_contract_version"], 1)
        _, upgraded = load_project(self.project)
        self.assertEqual(upgraded["current_build"], "build-0001")
        self.assertIsNone(upgraded["accepted_build"])
        self.assertEqual(upgraded["generation"]["pre_v2_accepted_build"], "build-0001")

    def test_legacy_v2_upgrade_archives_the_old_pointer_for_integrity_rebaseline(self) -> None:
        _, project = load_project(self.project)
        build_dir = self.project / "builds" / "build-0001"
        build_dir.mkdir()
        (build_dir / "build.json").write_text(
            json.dumps(
                {
                    "build_id": "build-0001",
                    "pet_id": project["id"],
                    "build_inputs": {"look_metadata_fingerprint": "f" * 64},
                }
            ),
            encoding="utf-8",
        )
        project["current_build"] = "build-0001"
        project["accepted_build"] = "build-0001"
        project["look"]["cardinals"] = {"approved": True}
        project["look"]["row_9_approved"] = True
        project["look"]["row_9_approval"] = {"row_sha256": "a" * 64}
        save_project(self.project, project)

        status = subprocess.run(
            [sys.executable, "-m", "petkit", "status", "--project", str(self.project)],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
            text=True,
        )
        status_payload = json.loads(status.stdout)
        self.assertIn("run upgrade-project", status_payload["preflight"]["build"]["blockers"][0]["message"])

        result = upgrade_project(self.project)
        self.assertTrue(result["integrity_rebaseline"])
        _, upgraded = load_project(self.project)
        self.assertEqual(upgraded["current_build"], "build-0001")
        self.assertIsNone(upgraded["accepted_build"])
        self.assertEqual(upgraded["generation"]["pre_integrity_accepted_build"], "build-0001")
        self.assertIsNone(upgraded["look"]["cardinals"])
        self.assertFalse(upgraded["look"]["row_9_approved"])

    def test_build_edit_install_backup_rollback_and_variant_isolation(self) -> None:
        self.ingest()
        first = build_project(self.project)
        self.assertTrue(first["validation"]["ok"])
        first_dir = Path(first["build_dir"])
        first_validation = json.loads((first_dir / "validation.json").read_text(encoding="utf-8"))
        self.assertEqual(first_validation["file"], "spritesheet.webp")
        self.assertTrue((first_dir / "contact-sheet.png").is_file())
        self.assertTrue((first_dir / "frame-inspection.json").is_file())
        self.assertTrue(first["frame_inspection"]["ok"])
        self.assertEqual(len(list((first_dir / "previews").glob("*.gif"))), 11)
        self.assertEqual(len(list((first_dir / "qa" / "standard-filmstrips").glob("*.png"))), 9)
        self.assertTrue((first_dir / "qa" / "semantic-recognition" / "semantic-manifest.json").is_file())
        self.assertTrue((first_dir / "qa" / "semantic-recognition" / "semantic-full-sheet.png").is_file())
        self.assertTrue((first_dir / "qa" / "semantic-recognition" / "semantic-thumbnail-sheet.png").is_file())
        self.assertTrue((first_dir / "qa-private" / "semantic-recognition-answer-key.json").is_file())
        semantic_answer_key = json.loads((first_dir / "qa-private" / "semantic-recognition-answer-key.json").read_text())
        self.assertEqual(len(semantic_answer_key["controls"]), 4)
        self.assertEqual(json.loads((first_dir / "pet.json").read_text())["spriteVersionNumber"], 2)

        recovered_root = self.root / "recovered-projects"
        imported = subprocess.run(
            [
                sys.executable,
                "-m",
                "petkit",
                "import-package",
                "--package",
                str(first_dir),
                "--root",
                str(recovered_root),
                "--id",
                "recovered-moth",
            ],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
            text=True,
        )
        recovered_project_dir = Path(json.loads(imported.stdout)["project"])
        with patch("petkit.cli.extract_atlas_frames", side_effect=OSError("injected import failure")):
            with self.assertRaisesRegex(OSError, "injected import failure"):
                cmd_import(
                    SimpleNamespace(
                        package=first_dir,
                        root=recovered_root,
                        id="failed-recovery",
                    )
                )
        self.assertFalse((recovered_root / "failed-recovery").exists())
        self.assertEqual(list(recovered_root.glob(".failed-recovery.import-*")), [])
        _, recovered_project = load_project(recovered_project_dir)
        self.assertIsNone(recovered_project["look"]["mechanics"])
        self.assertIsNone(recovered_project["look"]["cardinals"])
        self.assertFalse(recovered_project["look"]["row_9_approved"])
        self.assertIsNone(recovered_project["accepted_build"])
        self.assertEqual(
            recovered_project["generation"]["recovery_import"]["baseline_mode"],
            "repairable-recovery",
        )
        recovered_status = subprocess.run(
            [sys.executable, "-m", "petkit", "status", "--project", str(recovered_project_dir)],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertFalse(json.loads(recovered_status.stdout)["ready_to_build"])
        recovered_build_dir = recovered_project_dir / "builds" / "build-0001"
        recovered_build_dir.mkdir()
        (recovered_build_dir / "build.json").write_text(
            json.dumps(
                {
                    "build_id": "build-0001",
                    "pet_id": recovered_project["id"],
                    "source_sha256": {},
                    "build_inputs": {"authority_fingerprint": "a" * 64},
                }
            ),
            encoding="utf-8",
        )
        recovered_project["current_build"] = "build-0001"
        save_project(recovered_project_dir, recovered_project)
        with self.assertRaisesRegex(ValueError, "accepted child-local baseline"):
            plan_edit(
                recovered_project_dir,
                "deterministic",
                "Try to edit recovered pixels before establishing local authority.",
                ["idle"],
            )

        manifest_path = first_dir / "pet.json"
        build_record_path = first_dir / "build.json"
        original_manifest = manifest_path.read_bytes()
        original_build_record = build_record_path.read_bytes()
        _, project_metadata = load_project(self.project)
        for name, field, value, error in (
            ("id", "id", "other-moth", "manifest id"),
            ("version", "spriteVersionNumber", 1, "not a V2"),
            ("path", "spritesheetPath", "../outside.webp", "spritesheetPath"),
        ):
            with self.subTest(manifest_drift=name):
                try:
                    manifest = json.loads(original_manifest)
                    manifest[field] = value
                    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                    build_record = json.loads(original_build_record)
                    build_record["pet_json_sha256"] = sha256_file(manifest_path)
                    build_record_path.write_text(json.dumps(build_record), encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, error):
                        _verify_build_artifact(self.project, project_metadata, str(first["build_id"]))
                finally:
                    manifest_path.write_bytes(original_manifest)
                    build_record_path.write_bytes(original_build_record)
        with self.subTest(manifest_drift="display-name-only"):
            try:
                manifest = json.loads(original_manifest)
                manifest["displayName"] = "A byte-drifted display name"
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "manifest no longer matches its immutable build record"):
                    _verify_build_artifact(self.project, project_metadata, str(first["build_id"]))
            finally:
                manifest_path.write_bytes(original_manifest)
        with self.subTest(manifest_drift="recorded-hash"):
            try:
                build_record = json.loads(original_build_record)
                build_record["pet_json_sha256"] = "0" * 64
                build_record_path.write_text(json.dumps(build_record), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "manifest no longer matches its immutable build record"):
                    _verify_build_artifact(self.project, project_metadata, str(first["build_id"]))
            finally:
                build_record_path.write_bytes(original_build_record)
        _verify_build_artifact(self.project, project_metadata, str(first["build_id"]))
        with self.assertRaisesRegex(ValueError, "invalid build id"):
            accept_build(self.project, "../build-0001", confirm_visual_qa=True, review_note="not reachable")
        original_atlas = (first_dir / "spritesheet.webp").read_bytes()
        (first_dir / "spritesheet.webp").write_bytes(original_atlas + b"tampered")
        with self.assertRaisesRegex(ValueError, "immutable build record"):
            accept_build(
                self.project,
                first["build_id"],
                confirm_visual_qa=True,
                review_note="A tampered artifact must never be accepted.",
            )
        (first_dir / "spritesheet.webp").write_bytes(original_atlas)
        with self.assertRaisesRegex(ValueError, "accepted build"):
            install_build(self.project, self.root / "premature-install")
        with self.assertRaisesRegex(ValueError, "complete published review package"):
            accept_build(
                self.project,
                first["build_id"],
                confirm_visual_qa=True,
                review_note="Direction review has not happened yet.",
            )
        direction_answer_key_path = first_dir / "qa-private" / "direction-blind-answer-key.json"
        direction_answer_key_bytes = direction_answer_key_path.read_bytes()
        drifted_direction_answer_key = json.loads(direction_answer_key_bytes)
        drifted_direction_answer_key["atlas_sha256"] = "0" * 64
        direction_answer_key_path.write_text(json.dumps(drifted_direction_answer_key), encoding="utf-8")
        try:
            with self.assertRaisesRegex(ValueError, "private review authority"):
                self.review(first)
        finally:
            direction_answer_key_path.write_bytes(direction_answer_key_bytes)
        with self.assertRaisesRegex(ValueError, "must be marked reviewer_independent"):
            self.review(first, reject_blind_independence=True)
        with self.assertRaisesRegex(ValueError, "distinct reviewer identifiers"):
            self.review(first, duplicate_blind_reviewers=True)
        with self.assertRaisesRegex(ValueError, "distinct submission files"):
            self.review(first, duplicate_blind_submissions=True)
        with self.assertRaisesRegex(ValueError, "must be distinct from blind direction reviewers"):
            self.review(first, direction_reviewer_id="blind-fixture-01")
        self.assertFalse((self.project / "reviews" / str(first["build_id"])).exists())
        self.assertEqual(
            list((self.project / "reviews").glob(f".{first['build_id']}.staging-*")),
            [],
        )
        with patch("petkit.build.append_event", side_effect=ValueError("injected unsafe review history")):
            self.review(first)
        first_review_dir = self.project / "reviews" / str(first["build_id"])
        review_summary_path = first_review_dir / "review-summary.json"
        review_summary_bytes = review_summary_path.read_bytes()
        mismatched_summary = json.loads(review_summary_bytes)
        mismatched_summary["continuity_review_required"] = not mismatched_summary["continuity_review_required"]
        review_summary_path.write_text(json.dumps(mismatched_summary), encoding="utf-8")
        try:
            with self.assertRaisesRegex(ValueError, "continuity requirement does not match"):
                accept_build(
                    self.project,
                    first["build_id"],
                    confirm_visual_qa=True,
                    review_note="Mutable summary cannot weaken an immutable continuity requirement.",
                )
        finally:
            review_summary_path.write_bytes(review_summary_bytes)
        for relative in (
            "qa-private/semantic-recognition-answer-key.json",
            "qa/direction-continuity.json",
        ):
            authority_path = first_dir / relative
            original_authority = authority_path.read_bytes()
            drifted_authority = json.loads(original_authority)
            drifted_authority["injected_drift"] = True
            authority_path.write_text(json.dumps(drifted_authority), encoding="utf-8")
            try:
                with self.subTest(review_authority_drift=relative):
                    with self.assertRaisesRegex(ValueError, "private review authority"):
                        accept_build(
                            self.project,
                            first["build_id"],
                            confirm_visual_qa=True,
                            review_note="Mutable review authority must invalidate acceptance.",
                        )
            finally:
                authority_path.write_bytes(original_authority)
        reviewed_status = subprocess.run(
            [sys.executable, "-m", "petkit", "status", "--project", str(self.project)],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
            text=True,
        )
        reviewed_status_payload = json.loads(reviewed_status.stdout)
        self.assertFalse(reviewed_status_payload["preflight"]["review"]["ok"])
        self.assertIn(
            "published review package already exists",
            reviewed_status_payload["preflight"]["review"]["blockers"][0]["message"],
        )
        self.assertTrue(reviewed_status_payload["preflight"]["accept"]["ok"])
        semantic_verdict = first_review_dir / "semantic-recognition-03.json"
        removed_semantic_verdict = self.root / "removed-semantic-recognition-03.json"
        semantic_verdict.replace(removed_semantic_verdict)
        try:
            with self.assertRaisesRegex(ValueError, "exactly three fresh semantic recognition verdicts"):
                accept_build(
                    self.project,
                    first["build_id"],
                    confirm_visual_qa=True,
                    review_note="Missing semantic evidence must invalidate a published review.",
                )
        finally:
            removed_semantic_verdict.replace(semantic_verdict)
        _, before_identity_drift = load_project(self.project)
        approved_look = json.loads(json.dumps(before_identity_drift["look"]))
        approve_identity(
            self.project,
            identity_image(self.root / "identity-b.png", color=(190, 70, 120)),
        )
        with self.assertRaisesRegex(ValueError, "canonical identity"):
            accept_build(
                self.project,
                first["build_id"],
                confirm_visual_qa=True,
                review_note="Identity drift must invalidate this review.",
            )
        _, identity_drift_project = load_project(self.project)
        self.assertEqual(identity_drift_project["current_build"], first["build_id"])
        self.assertIsNone(identity_drift_project["accepted_build"])
        self.assertIsNone(identity_drift_project["look"]["cardinals"])
        self.assertFalse(identity_drift_project["look"]["row_9_approved"])
        self.assertFalse((self.project / "history" / f"acceptance-{first['build_id']}.json").exists())
        approve_identity(self.project, self.root / "identity.png")
        _, restored_identity = load_project(self.project)
        restored_identity["look"] = approved_look
        save_project(self.project, restored_identity)
        with self.assertRaisesRegex(ValueError, "visual QA"):
            accept_build(self.project, first["build_id"])
        with patch("petkit.build.save_project", side_effect=OSError("injected acceptance commit failure")):
            with self.assertRaisesRegex(OSError, "acceptance commit failure"):
                accept_build(
                    self.project,
                    first["build_id"],
                    confirm_visual_qa=True,
                    review_note="The staged acceptance must not survive pointer failure.",
                )
        self.assertFalse((self.project / "history" / f"acceptance-{first['build_id']}.json").exists())
        _, failed_acceptance = load_project(self.project)
        self.assertIsNone(failed_acceptance["accepted_build"])
        with patch("petkit.build.append_event", side_effect=OSError("injected acceptance event failure")):
            accepted_first = accept_build(
                self.project,
                first["build_id"],
                confirm_visual_qa=True,
                review_note="Synthetic contact sheet and all nine previews passed the fixture rubric.",
            )
        self.assertEqual(len(accepted_first["post_commit_warnings"]), 1)
        self.assertIn("pointer were committed", accepted_first["post_commit_warnings"][0])
        continuity_path = first_dir / "qa" / "direction-continuity.json"
        continuity_bytes = continuity_path.read_bytes()
        continuity = json.loads(continuity_bytes)
        continuity["injected_install_drift"] = True
        continuity_path.write_text(json.dumps(continuity), encoding="utf-8")
        try:
            with self.assertRaisesRegex(ValueError, "private review authority"):
                install_build(self.project, self.root / "authority-drift-install")
        finally:
            continuity_path.write_bytes(continuity_bytes)

        with patch(
            "petkit.build._copy_package",
            side_effect=AssertionError("overlap validation must run before package copy"),
        ) as copy_package:
            for overlapping_root in (self.projects, self.project):
                with self.subTest(install_overlap=overlapping_root):
                    with self.assertRaisesRegex(ValueError, "must not be equal or contain one another"):
                        install_build(self.project, overlapping_root)
            copy_package.assert_not_called()
        _, overlap_project = load_project(self.project)
        self.assertEqual(overlap_project["current_build"], first["build_id"])
        self.assertEqual(overlap_project["accepted_build"], first["build_id"])

        install_root = self.root / "installed"
        with patch("petkit.build.atomic_write_json", side_effect=OSError("injected install journal failure")):
            installed_first = install_build(self.project, install_root)
        self.assertTrue(installed_first["committed"])
        self.assertEqual(len(installed_first["post_commit_warnings"]), 1)
        self.assertIn("filesystem operation committed", installed_first["post_commit_warnings"][0])
        self.assertIsNone(installed_first["backup"])
        first_installed_hash = sha256_file(install_root / "test-moth" / "spritesheet.webp")

        waving_before = sha256_file(self.project / "source" / "frames" / "waving" / "00.png")
        unplanned_replacement = replacement_frame(
            self.root / "unplanned-replacement.png",
            self.contract,
            (180, 95, 220, 255),
        )
        unplanned = subprocess.run(
            [
                sys.executable,
                "-m",
                "petkit",
                "replace-frame",
                "--project",
                str(self.project),
                "--state",
                "waving",
                "--index",
                "0",
                "--image",
                str(unplanned_replacement),
            ],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
            text=True,
        )
        unplanned_record = json.loads(unplanned.stdout)
        with patch("petkit.build.assemble_v2") as assemble:
            with self.assertRaisesRegex(ValueError, "plan-edit is required"):
                build_project(self.project)
            assemble.assert_not_called()
        self.assertFalse((self.project / "builds" / "build-0002").exists())
        _, unplanned_project = load_project(self.project)
        self.assertEqual(unplanned_project["current_build"], first["build_id"])
        self.assertEqual(unplanned_project["accepted_build"], first["build_id"])
        self.assertIsNone(unplanned_project["active_edit"])
        subprocess.run(
            [
                sys.executable,
                "-m",
                "petkit",
                "restore-frame",
                "--project",
                str(self.project),
                "--state",
                "waving",
                "--index",
                "0",
                "--backup",
                unplanned_record["backup"],
            ],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(sha256_file(self.project / "source" / "frames" / "waving" / "00.png"), waving_before)

        edit_scope = plan_edit(
            self.project,
            "deterministic",
            "Replace exactly one waving frame.",
            ["waving"],
            ["Every other state remains frame-identical."],
        )
        self.assertEqual(edit_scope["initial_baseline_build"], first["build_id"])

        idle_before = sha256_file(self.project / "source" / "frames" / "idle" / "00.png")
        running_before = sha256_file(self.project / "source" / "frames" / "running" / "00.png")
        replacement = replacement_frame(self.root / "replacement.png", self.contract, (230, 80, 90, 255))
        replaced = subprocess.run(
            [
                sys.executable,
                "-m",
                "petkit",
                "replace-frame",
                "--project",
                str(self.project),
                "--state",
                "waving",
                "--index",
                "0",
                "--image",
                str(replacement),
            ],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
            text=True,
        )
        replacement_record = json.loads(replaced.stdout)
        work_in_progress_status = subprocess.run(
            [sys.executable, "-m", "petkit", "status", "--project", str(self.project)],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertTrue(json.loads(work_in_progress_status.stdout)["preflight"]["install"]["ok"])
        work_in_progress_install = install_build(self.project, install_root)
        self.assertTrue(work_in_progress_install["ok"])
        self.assertEqual(work_in_progress_install["build_id"], first["build_id"])
        candidate = build_project(self.project, draft=True)
        self.assertEqual(candidate["build_kind"], "candidate")
        candidate_record = json.loads((Path(candidate["build_dir"]) / "build.json").read_text(encoding="utf-8"))
        self.assertEqual(candidate_record["artifact_reuse"]["parent_build"], first["build_id"])
        self.assertIn("idle", candidate_record["artifact_reuse"]["preview_states"])
        self.assertNotIn("waving", candidate_record["artifact_reuse"]["preview_states"])
        self.assertEqual(candidate_record["direction_qa"], {})
        with self.assertRaisesRegex(ValueError, "candidate builds"):
            accept_build(self.project, candidate["build_id"], confirm_visual_qa=True, review_note="candidate")
        _, candidate_project = load_project(self.project)
        self.assertEqual(candidate_project["current_build"], first["build_id"])
        second = build_project(self.project)
        self.assertEqual(second["change_report"]["changed_states"], {"waving": [0]})
        self.assertTrue(second["change_report"]["edit_scope"]["scope_ok"])
        second_dir = Path(second["build_dir"])
        self.assertTrue((second_dir / "before-after.png").is_file())
        persisted_change = json.loads((second_dir / "change-report.json").read_text(encoding="utf-8"))
        self.assertEqual(persisted_change["before"], f"../{first['build_id']}/spritesheet.webp")
        self.assertEqual(persisted_change["after"], "spritesheet.webp")
        _, review_project = load_project(self.project)
        self.assertEqual(review_project["current_build"], second["build_id"])
        self.assertEqual(review_project["accepted_build"], first["build_id"])
        self.assertEqual(sha256_file(self.project / "source" / "frames" / "idle" / "00.png"), idle_before)
        self.assertEqual(sha256_file(self.project / "source" / "frames" / "running" / "00.png"), running_before)
        parent_answer_key = first_dir / "qa-private" / "direction-blind-answer-key.json"
        parent_answer_key_bytes = parent_answer_key.read_bytes()
        drifted_parent_answer_key = json.loads(parent_answer_key_bytes)
        drifted_parent_answer_key["injected_inherited_drift"] = True
        parent_answer_key.write_text(json.dumps(drifted_parent_answer_key), encoding="utf-8")
        try:
            with self.assertRaisesRegex(ValueError, "private review authority"):
                self.review(second, inherit_direction_from=str(first["build_id"]))
        finally:
            parent_answer_key.write_bytes(parent_answer_key_bytes)
        with self.assertRaisesRegex(ValueError, "semantic recognition verdict must be independent and passing"):
            self.review(
                second,
                inherit_direction_from=str(first["build_id"]),
                reject_semantic=True,
            )
        second_review_dir = self.project / "reviews" / str(second["build_id"])
        self.assertFalse(second_review_dir.exists())
        self.assertEqual(
            list(second_review_dir.parent.glob(f".{second['build_id']}.staging-*")),
            [],
        )
        with self.assertRaisesRegex(ValueError, "complete published review package"):
            accept_build(
                self.project,
                second["build_id"],
                confirm_visual_qa=True,
                review_note="A failed inherited review must not be acceptable.",
            )
        _, failed_review_project = load_project(self.project)
        self.assertEqual(failed_review_project["accepted_build"], first["build_id"])
        self.assertEqual(failed_review_project["current_build"], second["build_id"])
        self.assertIsNotNone(failed_review_project["active_edit"])
        self.review(second, inherit_direction_from=first["build_id"])
        second_review = json.loads(
            (self.project / "reviews" / str(second["build_id"]) / "review-summary.json").read_text(encoding="utf-8")
        )
        self.assertTrue(second_review["direction_review_inherited"])
        inherited_direction = second_review_dir / "inherited-direction"
        removed_inherited_direction = self.root / f"removed-{second['build_id']}-inherited-direction"
        inherited_direction.replace(removed_inherited_direction)
        try:
            with self.assertRaisesRegex(ValueError, "inherited direction evidence is missing or incomplete"):
                accept_build(
                    self.project,
                    second["build_id"],
                    confirm_visual_qa=True,
                    review_note="Missing inherited evidence must invalidate lineage.",
                )
            _, broken_lineage_project = load_project(self.project)
            self.assertEqual(broken_lineage_project["accepted_build"], first["build_id"])
            self.assertEqual(broken_lineage_project["current_build"], second["build_id"])
            self.assertIsNotNone(broken_lineage_project["active_edit"])
        finally:
            removed_inherited_direction.replace(inherited_direction)
        accept_build(
            self.project,
            second["build_id"],
            confirm_visual_qa=True,
            review_note="The expected waving-only change passed synthetic visual review.",
        )
        _, accepted_project = load_project(self.project)
        self.assertIsNone(accepted_project["active_edit"])
        outside_extra = self.root / "outside-package-extra.txt"
        outside_extra.write_text("must not be copied through a descendant symlink", encoding="utf-8")
        (install_root / "test-moth" / "extra-link").symlink_to(outside_extra)
        real_installed_copy = build_module._copy_installed_package
        observed_displaced_source = False

        def assert_displaced_before_backup(source: Path, destination: Path) -> dict[str, str]:
            nonlocal observed_displaced_source
            if destination.parent == self.project / "backups" / "installed":
                observed_displaced_source = True
                self.assertTrue(source.name.startswith(".test-moth.previous-"))
                self.assertFalse((install_root / "test-moth").exists())
            return real_installed_copy(source, destination)

        with patch("petkit.build._copy_installed_package", side_effect=assert_displaced_before_backup):
            installed_second = install_build(self.project, install_root)
        self.assertTrue(observed_displaced_source)
        self.assertIsNotNone(installed_second["backup"])
        second_backup = Path(str(installed_second["backup"]))
        self.assertTrue((second_backup / ".petkit-backup.json").is_file())
        self.assertFalse((second_backup / "extra-link").exists())
        second_installed_hash = sha256_file(install_root / "test-moth" / "spritesheet.webp")
        self.assertNotEqual(second_installed_hash, first_installed_hash)
        backup_spritesheet = second_backup / "spritesheet.webp"
        backup_spritesheet_bytes = backup_spritesheet.read_bytes()
        backup_spritesheet.write_bytes(backup_spritesheet_bytes + b"corrupt")
        try:
            with self.assertRaisesRegex(ValueError, "recorded integrity hashes"):
                rollback_install(self.project, install_root, second_backup)
        finally:
            backup_spritesheet.write_bytes(backup_spritesheet_bytes)
        (second_backup / "ignored-extra-link").symlink_to(outside_extra)
        with patch(
            "petkit.build.shutil.copytree",
            side_effect=AssertionError("overlap validation must run before rollback copy"),
        ) as copytree:
            for overlapping_root in (self.projects, self.project):
                with self.subTest(rollback_overlap=overlapping_root):
                    with self.assertRaisesRegex(ValueError, "must not be equal or contain one another"):
                        rollback_install(
                            self.project,
                            overlapping_root,
                            Path(str(installed_second["backup"])),
                        )
            copytree.assert_not_called()
        self.assertEqual(sha256_file(install_root / "test-moth" / "spritesheet.webp"), second_installed_hash)
        with patch("petkit.build.append_event", side_effect=OSError("injected rollback event failure")):
            rolled_back = rollback_install(self.project, install_root)
        self.assertTrue(rolled_back["committed"])
        self.assertEqual(len(rolled_back["post_commit_warnings"]), 1)
        self.assertIn("filesystem operation committed", rolled_back["post_commit_warnings"][0])
        self.assertEqual(sha256_file(install_root / "test-moth" / "spritesheet.webp"), first_installed_hash)
        self.assertEqual(rolled_back["restored_backup"], installed_second["backup"])
        self.assertIsNotNone(rolled_back["displaced_backup"])
        self.assertFalse((install_root / "test-moth" / "ignored-extra-link").exists())

        real_rmtree = shutil.rmtree

        def fail_displaced_cleanup(path: str | Path, *args: object, **kwargs: object) -> None:
            if Path(path).name.startswith(".test-moth.pre-rollback-"):
                raise OSError("injected rollback displaced-package cleanup failure")
            real_rmtree(path, *args, **kwargs)

        with patch("petkit.build.shutil.rmtree", side_effect=fail_displaced_cleanup):
            cleanup_result = rollback_install(
                self.project,
                install_root,
                Path(str(rolled_back["displaced_backup"])),
            )
        self.assertTrue((install_root / "test-moth").is_dir())
        self.assertEqual(sha256_file(install_root / "test-moth" / "spritesheet.webp"), second_installed_hash)
        self.assertEqual(list(install_root.glob(".test-moth.rollback-*")), [])
        self.assertIsNotNone(cleanup_result["cleanup_pending"])
        real_rmtree(Path(str(cleanup_result["cleanup_pending"])))
        self.assertEqual(list(install_root.glob(".test-moth.pre-rollback-*")), [])
        with self.assertRaisesRegex(ValueError, "direct child of this project's installed backups"):
            rollback_install(self.project, install_root, self.root)

        variant = create_variant(self.project, self.projects, "test-moth-winter", "Test Moth — Winter")
        subprocess.run(
            [
                sys.executable,
                "-m",
                "petkit",
                "restore-frame",
                "--project",
                str(self.project),
                "--state",
                "waving",
                "--index",
                "0",
                "--backup",
                replacement_record["backup"],
            ],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(sha256_file(self.project / "source" / "frames" / "waving" / "00.png"), waving_before)

        original_hash = sha256_file(self.project / "source" / "frames" / "idle" / "00.png")
        variant_frame = variant / "source" / "frames" / "idle" / "00.png"
        variant_frame_bytes = variant_frame.read_bytes()
        replacement_frame(variant_frame, self.contract, (240, 245, 255, 255))
        self.assertEqual(sha256_file(self.project / "source" / "frames" / "idle" / "00.png"), original_hash)
        self.assertNotEqual(sha256_file(variant_frame), original_hash)
        _, variant_metadata = load_project(variant)
        self.assertEqual(variant_metadata["parent_id"], "test-moth")
        self.assertIsNone(variant_metadata["current_build"])
        with self.assertRaisesRegex(ValueError, "variant source changed before its first child-local baseline"):
            build_project(variant)
        variant_frame.write_bytes(variant_frame_bytes)
        approve_identity(
            variant,
            identity_image(self.root / "variant-identity-drift.png", color=(30, 190, 160)),
        )
        with self.assertRaisesRegex(ValueError, "variant authority changed before its first child-local baseline"):
            build_project(variant)
        variant_build_dir = variant / "builds" / "build-0001"
        variant_build_dir.mkdir()
        (variant_build_dir / "build.json").write_text(
            json.dumps(
                {
                    "build_id": "build-0001",
                    "pet_id": variant_metadata["id"],
                    "source_sha256": {},
                    "build_inputs": {"authority_fingerprint": "a" * 64},
                }
            ),
            encoding="utf-8",
        )
        variant_metadata["current_build"] = "build-0001"
        save_project(variant, variant_metadata)
        with self.assertRaisesRegex(ValueError, "accepted child-local baseline"):
            plan_edit(
                variant,
                "variant",
                "Try to edit the variant before establishing its child-local baseline.",
                ["idle"],
            )

    def test_targeted_row_replacement_and_restore_preserve_other_states(self) -> None:
        self.ingest()
        idle_before = {
            path.name: sha256_file(path)
            for path in sorted((self.project / "source" / "frames" / "idle").glob("*.png"))
        }
        review_before = {
            path.name: sha256_file(path)
            for path in sorted((self.project / "source" / "frames" / "review").glob("*.png"))
        }
        replacement_strip = row_strip(
            self.root / "idle-repair.png",
            self.contract.state("idle"),
            self.contract,
            color_seed=77,
        )
        replaced = subprocess.run(
            [
                sys.executable,
                "-m",
                "petkit",
                "ingest-row",
                "--project",
                str(self.project),
                "--state",
                "idle",
                "--strip",
                str(replacement_strip),
                "--method",
                "components",
                "--chroma-threshold",
                "60",
            ],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
            text=True,
        )
        replacement_record = json.loads(replaced.stdout)
        idle_after = {
            path.name: sha256_file(path)
            for path in sorted((self.project / "source" / "frames" / "idle").glob("*.png"))
        }
        self.assertNotEqual(idle_after, idle_before)
        self.assertEqual(
            {
                path.name: sha256_file(path)
                for path in sorted((self.project / "source" / "frames" / "review").glob("*.png"))
            },
            review_before,
        )
        subprocess.run(
            [
                sys.executable,
                "-m",
                "petkit",
                "restore-row",
                "--project",
                str(self.project),
                "--state",
                "idle",
                "--backup",
                replacement_record["backup"],
            ],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            {
                path.name: sha256_file(path)
                for path in sorted((self.project / "source" / "frames" / "idle").glob("*.png"))
            },
            idle_before,
        )

    def test_recorded_edit_scope_rejects_an_unexpected_state(self) -> None:
        self.ingest()
        baseline = build_project(self.project)
        self.review(baseline)
        accept_build(
            self.project,
            baseline["build_id"],
            confirm_visual_qa=True,
            review_note="Synthetic baseline is suitable for edit-scope enforcement.",
        )
        plan_edit(self.project, "deterministic", "Change waving only.", ["waving"])
        replacement = replacement_frame(self.root / "unexpected.png", self.contract, (90, 220, 120, 255))
        subprocess.run(
            [
                sys.executable,
                "-m",
                "petkit",
                "replace-frame",
                "--project",
                str(self.project),
                "--state",
                "idle",
                "--index",
                "0",
                "--image",
                str(replacement),
            ],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
            text=True,
        )
        with patch("petkit.build.assemble_v2") as assemble:
            with self.assertRaisesRegex(ValueError, "outside its recorded scope before build: idle"):
                build_project(self.project)
            assemble.assert_not_called()
        self.assertFalse((self.project / "builds" / "build-0002").exists())
        _, project = load_project(self.project)
        self.assertEqual(project["current_build"], baseline["build_id"])


if __name__ == "__main__":
    unittest.main()
