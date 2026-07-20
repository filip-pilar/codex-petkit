from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from petkit.build import accept_build, build_project, install_build, review_directions, rollback_install
from petkit.contract import load_contract
from petkit.imageops import extract_row_strip
from petkit.project import approve_identity, create_variant, init_project, load_project, plan_edit, save_project, sha256_file
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
            project["look"] = {
                "mechanics": {"path": "synthetic-fixture"},
                "cardinals": {"approved": True, "path": "synthetic-fixture"},
                "row_9_approved": True,
                "row_9_approval": {"review_note": "Synthetic coherent row fixture."},
            }
        save_project(self.project, project)

    def review(self, build: dict[str, object]) -> None:
        build_dir = Path(str(build["build_dir"]))
        atlas_hash = sha256_file(build_dir / "spritesheet.webp")
        answer = json.loads((build_dir / "qa-private" / "direction-blind-answer-key.json").read_text(encoding="utf-8"))
        verdict = {
            "pairs": [
                {"pair": pair["pair"], "A": pair["A"]["expected_direction"], "B": pair["B"]["expected_direction"]}
                for pair in answer["pairs"]
            ]
        }
        verdicts = []
        for index in range(3):
            path = self.root / f"blind-{index}.json"
            path.write_text(json.dumps(verdict), encoding="utf-8")
            verdicts.append(path)
        semantics = self.root / "semantics.json"
        semantics.write_text(
            json.dumps(
                {
                    "atlas_sha256": atlas_hash,
                    "reviewer_independent": True,
                    "directions": [
                        {"degrees": degrees, "observed": f"fixture-{degrees}", "pass": True}
                        for degrees in self.contract.look_directions_degrees
                    ],
                }
            ),
            encoding="utf-8",
        )
        semantic_answer = json.loads((build_dir / "qa-private" / "semantic-recognition-answer-key.json").read_text(encoding="utf-8"))
        semantic = self.root / "semantic.json"
        semantic.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "atlas_sha256": atlas_hash,
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
        )

    def test_partial_work_is_resumable(self) -> None:
        self.ingest(["idle", "running-right"])
        _, project = load_project(self.project)
        self.assertEqual(project["generation"]["completed_states"], ["idle", "running-right"])
        self.assertTrue((self.project / "source" / "frames" / "idle" / "00.png").is_file())
        self.ingest()
        _, resumed = load_project(self.project)
        self.assertEqual(len(resumed["generation"]["completed_states"]), 11)

    def test_build_requires_passing_semantic_design_gates(self) -> None:
        self.ingest()
        (self.project / "qa" / "key-pose-review.json").unlink()
        with self.assertRaisesRegex(ValueError, "key-pose-review.json"):
            build_project(self.project)

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
        with self.assertRaisesRegex(ValueError, "direction review"):
            accept_build(
                self.project,
                first["build_id"],
                confirm_visual_qa=True,
                review_note="Direction review has not happened yet.",
            )
        self.review(first)
        with self.assertRaisesRegex(ValueError, "visual QA"):
            accept_build(self.project, first["build_id"])
        accept_build(
            self.project,
            first["build_id"],
            confirm_visual_qa=True,
            review_note="Synthetic contact sheet and all nine previews passed the fixture rubric.",
        )

        install_root = self.root / "installed"
        installed_first = install_build(self.project, install_root)
        self.assertIsNone(installed_first["backup"])
        first_installed_hash = sha256_file(install_root / "test-moth" / "spritesheet.webp")

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
        waving_before = sha256_file(self.project / "source" / "frames" / "waving" / "00.png")
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
        second = build_project(self.project)
        self.assertEqual(second["change_report"]["changed_states"], {"waving": [0]})
        self.assertTrue(second["change_report"]["edit_scope"]["scope_ok"])
        second_dir = Path(second["build_dir"])
        self.assertTrue((second_dir / "before-after.png").is_file())
        persisted_change = json.loads((second_dir / "change-report.json").read_text(encoding="utf-8"))
        self.assertEqual(persisted_change["before"], "../build-0001/spritesheet.webp")
        self.assertEqual(persisted_change["after"], "spritesheet.webp")
        _, review_project = load_project(self.project)
        self.assertEqual(review_project["current_build"], second["build_id"])
        self.assertEqual(review_project["accepted_build"], first["build_id"])
        self.assertEqual(sha256_file(self.project / "source" / "frames" / "idle" / "00.png"), idle_before)
        self.assertEqual(sha256_file(self.project / "source" / "frames" / "running" / "00.png"), running_before)
        self.review(second)
        accept_build(
            self.project,
            second["build_id"],
            confirm_visual_qa=True,
            review_note="The expected waving-only change passed synthetic visual review.",
        )
        _, accepted_project = load_project(self.project)
        self.assertIsNone(accepted_project["active_edit"])
        installed_second = install_build(self.project, install_root)
        self.assertIsNotNone(installed_second["backup"])
        second_installed_hash = sha256_file(install_root / "test-moth" / "spritesheet.webp")
        self.assertNotEqual(second_installed_hash, first_installed_hash)
        rolled_back = rollback_install(self.project, install_root)
        self.assertEqual(sha256_file(install_root / "test-moth" / "spritesheet.webp"), first_installed_hash)
        self.assertEqual(rolled_back["restored_backup"], installed_second["backup"])
        self.assertIsNotNone(rolled_back["displaced_backup"])
        with self.assertRaisesRegex(ValueError, "inside this project's installed backups"):
            rollback_install(self.project, install_root, self.root)

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

        variant = create_variant(self.project, self.projects, "test-moth-winter", "Test Moth — Winter")
        original_hash = sha256_file(self.project / "source" / "frames" / "idle" / "00.png")
        variant_frame = variant / "source" / "frames" / "idle" / "00.png"
        replacement_frame(variant_frame, self.contract, (240, 245, 255, 255))
        self.assertEqual(sha256_file(self.project / "source" / "frames" / "idle" / "00.png"), original_hash)
        self.assertNotEqual(sha256_file(variant_frame), original_hash)
        _, variant_metadata = load_project(variant)
        self.assertEqual(variant_metadata["parent_id"], "test-moth")
        self.assertIsNone(variant_metadata["current_build"])

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
        with self.assertRaisesRegex(ValueError, "outside its recorded scope: idle"):
            build_project(self.project)
        self.assertFalse((self.project / "builds" / "build-0002").exists())
        _, project = load_project(self.project)
        self.assertEqual(project["current_build"], baseline["build_id"])


if __name__ == "__main__":
    unittest.main()
