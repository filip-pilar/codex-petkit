from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from petkit.contract import load_contract
from petkit.imageops import (
    compare_atlases,
    compose_atlas,
    extract_atlas_frames,
    extract_row_strip,
    inspect_frames,
    validate_atlas,
)
from petkit.v2 import (
    CROSS_STATE_QUALITY_GATES,
    STANDARD_FRAME_BEATS,
    STANDARD_CONFUSION_PAIRS,
    STANDARD_FRAME_COUNTS,
    STANDARD_QUALITY_GATES,
    STANDARD_STATE_IDS,
    validate_semantic_recognition,
    validate_visual_qa,
)
from petkit.semantic import SEMANTIC_CONFUSION_PAIRS, SEMANTIC_STATE_OPTIONS


class ValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = load_contract(2)
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def valid_visual_qa(self) -> dict[str, object]:
        return {
            "atlas_sha256": "fixture-hash",
            "reviewer_id": "visual-fixture-01",
            "reviewer_independent": True,
            "pass": True,
            "note": "All fixture checks passed.",
            "review_inputs": {
                "canonical_identity_seen": True,
                "normal_size_filmstrips_seen": True,
                "animated_previews_seen": True,
                "prompts_or_motion_plan_seen": False,
            },
            "standard_states": [
                {
                    "state": state,
                    "observed_action": f"observed {state}",
                    "silhouette_signature": f"distinct {state} silhouette",
                    "frame_observations": [
                        {
                            "index": index,
                            "beat": STANDARD_FRAME_BEATS[state][index],
                            "support": "visible support/contact remains coherent",
                            "anatomy": "intact model",
                            "contribution": "distinct motion beat",
                        }
                        for index in range(STANDARD_FRAME_COUNTS[state])
                    ],
                    "transition_observations": [
                        {
                            "from": index,
                            "to": (index + 1) % STANDARD_FRAME_COUNTS[state],
                            "plausible": True,
                            "note": "physically continuous",
                        }
                        for index in range(STANDARD_FRAME_COUNTS[state])
                    ],
                    "quality_gates": {
                        gate: {"pass": True, "note": f"{gate} passed"}
                        for gate in STANDARD_QUALITY_GATES
                    },
                    "pass": True,
                    "note": "Readable at normal pet size.",
                }
                for state in STANDARD_STATE_IDS
            ],
            "confusion_pairs": [
                {
                    "states": list(pair),
                    "distinct": True,
                    "evidence": "Different silhouette and timing signatures.",
                }
                for pair in STANDARD_CONFUSION_PAIRS
            ],
            "cross_state_consistency": {
                gate: {"pass": True, "note": f"cross-state {gate} passed"}
                for gate in CROSS_STATE_QUALITY_GATES
            },
        }

    def test_visual_qa_requires_all_state_confusion_pairs_to_pass(self) -> None:
        payload = self.valid_visual_qa()
        payload["confusion_pairs"][1]["distinct"] = False  # type: ignore[index]
        with self.assertRaisesRegex(ValueError, "not visually distinct"):
            validate_visual_qa(payload, "fixture-hash")

    def test_visual_qa_accepts_complete_state_distinction_evidence(self) -> None:
        validate_visual_qa(self.valid_visual_qa(), "fixture-hash")

    def test_visual_qa_rejects_the_previous_locky_review_schema(self) -> None:
        payload = self.valid_visual_qa()
        del payload["review_inputs"]
        for state in payload["standard_states"]:  # type: ignore[union-attr]
            state.pop("frame_observations")
            state.pop("transition_observations")
            state.pop("quality_gates")
        payload["scale_consistency"] = {"pass": True, "note": "looked stable"}
        del payload["cross_state_consistency"]
        with self.assertRaisesRegex(ValueError, "without prompt or motion-plan leakage"):
            validate_visual_qa(payload, "fixture-hash")

    def test_visual_qa_rejects_one_bad_frame_transition(self) -> None:
        payload = self.valid_visual_qa()
        waiting = payload["standard_states"][6]  # type: ignore[index]
        waiting["transition_observations"][2]["plausible"] = False
        with self.assertRaisesRegex(ValueError, "transition 2->3 is not physically coherent"):
            validate_visual_qa(payload, "fixture-hash")

    def test_visual_qa_rejects_a_wrong_running_phase(self) -> None:
        payload = self.valid_visual_qa()
        running = payload["standard_states"][1]  # type: ignore[index]
        running["frame_observations"][5]["beat"] = "flight"
        with self.assertRaisesRegex(ValueError, "requires beat compression"):
            validate_visual_qa(payload, "fixture-hash")

    def test_visual_qa_rejects_anatomy_or_material_failure(self) -> None:
        payload = self.valid_visual_qa()
        working = payload["standard_states"][7]  # type: ignore[index]
        working["quality_gates"]["identity_material"]["pass"] = False
        with self.assertRaisesRegex(ValueError, "failed identity_material QA"):
            validate_visual_qa(payload, "fixture-hash")

    def valid_semantic_recognition(self) -> tuple[dict[str, object], dict[str, object]]:
        answer_key = {
            "schema_version": 1,
            "atlas_sha256": "fixture-hash",
            "clips": [
                {"token": f"clip-{index}", "state": state}
                for index, state in enumerate(STANDARD_STATE_IDS)
            ],
            "controls": [
                {"id": "control-01"},
                {"id": "control-02"},
            ],
        }
        payload = {
            "schema_version": 1,
            "atlas_sha256": "fixture-hash",
            "reviewer_id": "semantic-fixture-01",
            "reviewer_independent": True,
            "pass": True,
            "note": "Anonymous semantic recognition passed at both display sizes.",
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
                    "token": f"clip-{index}",
                    "full_state": state,
                    "thumbnail_state": state,
                    "full_alternative": "none",
                    "thumbnail_alternative": "none",
                    "full_evidence": "distinct full-size silhouette and rhythm",
                    "thumbnail_evidence": "distinct small-size silhouette and rhythm",
                }
                for index, state in enumerate(STANDARD_STATE_IDS)
            ],
            "pairwise_confusions": [
                {
                    "states": list(pair),
                    "full_distinct": True,
                    "thumbnail_distinct": True,
                    "evidence": "different silhouette and timing signatures",
                }
                for pair in SEMANTIC_CONFUSION_PAIRS
            ],
            "calibration": [
                {"id": "control-01", "result": "reject", "evidence": "inert control"},
                {"id": "control-02", "result": "reject", "evidence": "ambiguous control"},
            ],
        }
        return payload, answer_key

    def test_semantic_recognition_requires_anonymous_full_and_thumbnail_mapping(self) -> None:
        payload, answer_key = self.valid_semantic_recognition()
        validate_semantic_recognition(payload, answer_key, "fixture-hash")

    def test_semantic_recognition_rejects_a_weak_state_mapping(self) -> None:
        payload, answer_key = self.valid_semantic_recognition()
        payload["assignments"][6]["thumbnail_state"] = "idle"  # type: ignore[index]
        with self.assertRaisesRegex(ValueError, "misclassified at thumbnail size"):
            validate_semantic_recognition(payload, answer_key, "fixture-hash")

    def test_semantic_recognition_rejects_duplicate_clip_assignments(self) -> None:
        payload, answer_key = self.valid_semantic_recognition()
        payload["assignments"][1]["token"] = payload["assignments"][0]["token"]  # type: ignore[index]
        with self.assertRaisesRegex(ValueError, "exactly once"):
            validate_semantic_recognition(payload, answer_key, "fixture-hash")

    def test_semantic_recognition_rejects_missing_calibration_rejection(self) -> None:
        payload, answer_key = self.valid_semantic_recognition()
        payload["calibration"][0]["result"] = "pass"  # type: ignore[index]
        with self.assertRaisesRegex(ValueError, "was not rejected"):
            validate_semantic_recognition(payload, answer_key, "fixture-hash")

    def test_semantic_recognition_rejects_a_holdout_state_swap(self) -> None:
        payload, answer_key = self.valid_semantic_recognition()
        payload["assignments"][6]["full_state"], payload["assignments"][8]["full_state"] = (  # type: ignore[index]
            payload["assignments"][8]["full_state"],
            payload["assignments"][6]["full_state"],
        )
        with self.assertRaisesRegex(ValueError, "misclassified at full size"):
            validate_semantic_recognition(payload, answer_key, "fixture-hash")

    def test_wrong_dimensions_fail(self) -> None:
        path = self.root / "wrong.png"
        Image.new("RGBA", (100, 100), (0, 0, 0, 0)).save(path)
        report = validate_atlas(path, self.contract)
        self.assertFalse(report["ok"])
        self.assertIn("wrong-dimensions", {error["code"] for error in report["errors"]})

    def test_visible_unused_cell_fails(self) -> None:
        path = self.root / "unused.png"
        atlas = Image.new("RGBA", (self.contract.width, self.contract.height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(atlas)
        for state in self.contract.states:
            for column in range(state.frame_count):
                left = column * self.contract.cell_width + 40
                top = state.row * self.contract.cell_height + 40
                draw.rectangle((left, top, left + 50, top + 70), fill=(100, 80, 200, 255))
        waiting = self.contract.state("waiting")
        left = waiting.frame_count * self.contract.cell_width + 10
        top = waiting.row * self.contract.cell_height + 10
        draw.rectangle((left, top, left + 20, top + 20), fill=(255, 0, 0, 255))
        atlas.save(path)
        report = validate_atlas(path, self.contract)
        self.assertFalse(report["ok"])
        self.assertIn("nonempty-unused-cell", {error["code"] for error in report["errors"]})

    def test_pixel_identical_row_fails_frame_inspection(self) -> None:
        frames_root = self.root / "frames"
        for state in self.contract.states:
            state_dir = frames_root / state.id
            state_dir.mkdir(parents=True)
            frame = Image.new("RGBA", (self.contract.cell_width, self.contract.cell_height), (0, 0, 0, 0))
            draw = ImageDraw.Draw(frame)
            draw.rectangle((60, 50, 130, 170), fill=(90 + state.row, 80, 180, 255))
            for index in range(state.frame_count):
                current = frame.copy()
                if state.id != "idle":
                    ImageDraw.Draw(current).point((70 + index, 70), fill=(255, 255, 255, 255))
                current.save(state_dir / f"{index:02d}.png")
        report = inspect_frames(frames_root, self.contract)
        self.assertFalse(report["ok"])
        static_rows = {error.get("state") for error in report["errors"] if error["code"] == "static-row"}
        self.assertEqual(static_rows, {"idle"})

    def test_exact_duplicate_motion_beat_fails_frame_inspection(self) -> None:
        frames_root = self.root / "duplicate-frames"
        for state in self.contract.states:
            state_dir = frames_root / state.id
            state_dir.mkdir(parents=True)
            for index in range(state.frame_count):
                frame = Image.new("RGBA", (self.contract.cell_width, self.contract.cell_height), (0, 0, 0, 0))
                draw = ImageDraw.Draw(frame)
                draw.rectangle((50, 45, 140, 175), fill=(90 + state.row, 80, 180, 255))
                point_index = 0 if state.id == "waving" and index == state.frame_count - 1 else index
                draw.point((60 + point_index, 60), fill=(255, 255, 255, 255))
                frame.save(state_dir / f"{index:02d}.png")
        report = inspect_frames(frames_root, self.contract)
        duplicate_rows = {error.get("state") for error in report["errors"] if error["code"] == "duplicate-frame"}
        self.assertEqual(duplicate_rows, {"waving"})

    def test_standard_edge_contact_fails_frame_inspection(self) -> None:
        frames_root = self.root / "edge-frames"
        for state in self.contract.states:
            state_dir = frames_root / state.id
            state_dir.mkdir(parents=True)
            for index in range(state.frame_count):
                frame = Image.new("RGBA", (self.contract.cell_width, self.contract.cell_height), (0, 0, 0, 0))
                draw = ImageDraw.Draw(frame)
                left = 0 if state.id == "idle" else 50 + index
                draw.rectangle((left, 45, min(left + 70, self.contract.cell_width - 1), 175), fill=(90 + state.row, 80, 180, 255))
                frame.save(state_dir / f"{index:02d}.png")
        report = inspect_frames(frames_root, self.contract)
        edge_rows = {error.get("state") for error in report["errors"] if error["code"] == "edge-contact"}
        self.assertEqual(edge_rows, {"idle"})

    def test_extract_and_recompose_preserves_every_exact_cell(self) -> None:
        source = self.root / "source.png"
        atlas = Image.new("RGBA", (self.contract.width, self.contract.height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(atlas)
        for state in self.contract.states:
            for column in range(state.frame_count):
                left = column * self.contract.cell_width + 20 + column
                top = state.row * self.contract.cell_height + 16 + state.row
                draw.rounded_rectangle(
                    (left, top, left + 60 + state.row, top + 80 + column),
                    radius=12,
                    fill=(60 + state.row * 8, 80 + column * 7, 180, 255),
                )
        idle = self.contract.state("idle")
        neutral_source = atlas.crop((0, 0, self.contract.cell_width, self.contract.cell_height))
        atlas.alpha_composite(neutral_source, (idle.frame_count * self.contract.cell_width, 0))
        atlas.save(source)
        frames = self.root / "exact-frames"
        extract_atlas_frames(source, frames, self.contract)
        rebuilt = self.root / "rebuilt.png"
        compose_atlas(frames, rebuilt, self.contract)
        report = compare_atlases(source, rebuilt, self.contract)
        self.assertEqual(report["changed_states"], {})
        self.assertEqual(report["unchanged_states"], [state.id for state in self.contract.states])

    def test_auto_extraction_preserves_jump_height(self) -> None:
        state = self.contract.state("jumping")
        strip = Image.new(
            "RGB",
            (state.frame_count * self.contract.cell_width, self.contract.cell_height),
            (0, 255, 0),
        )
        draw = ImageDraw.Draw(strip)
        tops = [100, 62, 20, 62, 100]
        for index, top in enumerate(tops):
            left = index * self.contract.cell_width + 58
            draw.rounded_rectangle((left, top, left + 76, top + 90), radius=20, fill=(80, 60, 190))
        strip_path = self.root / "jump-strip.png"
        strip.save(strip_path)
        output = self.root / "jump-frames"
        report = extract_row_strip(strip_path, output, state, self.contract, "#00FF00", 60.0, "auto")
        self.assertEqual(report["method"], "stable-slots")
        baselines = []
        for path in sorted(output.glob("*.png")):
            with Image.open(path) as frame:
                baselines.append(frame.convert("RGBA").getbbox()[3])
        self.assertGreater(max(baselines) - min(baselines), 40)

    def test_motion_component_extraction_preserves_scale_and_safe_jump_travel(self) -> None:
        state = self.contract.state("jumping")
        strip = Image.new(
            "RGB",
            (state.frame_count * self.contract.cell_width, self.contract.cell_height),
            (0, 255, 0),
        )
        draw = ImageDraw.Draw(strip)
        tops = [96, 84, 62, 84, 96]
        for index, top in enumerate(tops):
            left = index * self.contract.cell_width + 58
            draw.rounded_rectangle((left, top, left + 76, top + 96), radius=20, fill=(80, 60, 190))
        strip_path = self.root / "jump-motion-strip.png"
        strip.save(strip_path)
        output = self.root / "jump-motion-frames"
        report = extract_row_strip(
            strip_path,
            output,
            state,
            self.contract,
            "#00FF00",
            60.0,
            "motion-components",
        )
        self.assertEqual(report["method"], "motion-components")
        heights = []
        baselines = []
        for path in sorted(output.glob("*.png")):
            with Image.open(path) as frame:
                bbox = frame.convert("RGBA").getbbox()
            self.assertIsNotNone(bbox)
            heights.append(bbox[3] - bbox[1])
            baselines.append(bbox[3])
        self.assertLessEqual(max(heights) - min(heights), 1)
        self.assertGreaterEqual(max(baselines) - min(baselines), 18)
        self.assertLessEqual(max(baselines) - min(baselines), 20)

    def test_standard_only_to_v2_comparison_reports_only_added_look_rows(self) -> None:
        standard = self.root / "standard.png"
        extended = self.root / "extended.png"
        base = Image.new(
            "RGBA",
            (self.contract.width, self.contract.standard_rows * self.contract.cell_height),
            (0, 0, 0, 0),
        )
        draw = ImageDraw.Draw(base)
        for state in self.contract.standard_states:
            for column in range(state.frame_count):
                left = column * self.contract.cell_width + 50
                top = state.row * self.contract.cell_height + 50
                draw.rectangle((left, top, left + 40, top + 60), fill=(30 + state.row, 70, 190, 255))
        base.save(standard)
        full = Image.new("RGBA", (self.contract.width, self.contract.height), (0, 0, 0, 0))
        full.alpha_composite(base)
        full_draw = ImageDraw.Draw(full)
        for state in self.contract.look_states:
            for column in range(state.frame_count):
                left = column * self.contract.cell_width + 52
                top = state.row * self.contract.cell_height + 52
                full_draw.rectangle((left, top, left + 40, top + 60), fill=(60, 80 + column, 180, 255))
        full.save(extended)
        report = compare_atlases(standard, extended, self.contract)
        self.assertEqual(report["changed_states"], {})
        self.assertEqual(report["added_states"], ["look-a", "look-b"])
        self.assertEqual(report["unchanged_states"], [state.id for state in self.contract.standard_states])


if __name__ == "__main__":
    unittest.main()
