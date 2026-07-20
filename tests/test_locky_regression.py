from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from petkit.contract import load_contract
from petkit.semantic import (
    SEMANTIC_CONFUSION_PAIRS,
    SEMANTIC_STATE_OPTIONS,
    validate_design_gate_artifacts,
)
from petkit.v2 import STANDARD_STATE_IDS, validate_semantic_recognition


class SemanticRegressionTests(unittest.TestCase):
    """Keep historical quality failures reproducible without production art."""

    def test_idle_like_state_substitution_is_rejected_anonymously(self) -> None:
        atlas_hash = "synthetic-regression-atlas"
        answer_key = {
            "schema_version": 1,
            "atlas_sha256": atlas_hash,
            "clips": [
                {"token": f"clip-{index}", "state": state}
                for index, state in enumerate(STANDARD_STATE_IDS)
            ],
            "controls": [
                {"id": "control-inert"},
                {"id": "control-identity-drift"},
            ],
        }
        assignments = []
        for index, expected in enumerate(STANDARD_STATE_IDS):
            observed = "idle" if expected in {"waiting", "running", "review"} else expected
            assignments.append(
                {
                    "token": f"clip-{index}",
                    "full_state": observed,
                    "thumbnail_state": observed,
                    "full_alternative": "idle" if observed != "idle" else "none",
                    "thumbnail_alternative": "idle" if observed != "idle" else "none",
                    "full_evidence": "The synthetic verdict substitutes a generic idle-like read.",
                    "thumbnail_evidence": "The same substitution remains at UI size.",
                }
            )
        verdict = {
            "schema_version": 1,
            "atlas_sha256": atlas_hash,
            "reviewer_id": "semantic-regression-fixture-01",
            "reviewer_independent": True,
            "pass": True,
            "note": "A deliberately over-claimed verdict used as a regression fixture.",
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
            "assignments": assignments,
            "pairwise_confusions": [
                {
                    "states": list(pair),
                    "full_distinct": True,
                    "thumbnail_distinct": True,
                    "evidence": "Deliberately over-claimed distinction evidence.",
                }
                for pair in SEMANTIC_CONFUSION_PAIRS
            ],
            "calibration": [
                {"id": control["id"], "result": "reject", "evidence": "Synthetic invalid control."}
                for control in answer_key["controls"]
            ],
        }

        with self.assertRaisesRegex(ValueError, "misclassified"):
            validate_semantic_recognition(verdict, answer_key, atlas_hash)

    def test_build_design_gate_rejects_a_missing_key_pose_sheet(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            qa = project / "qa"
            qa.mkdir()
            (qa / "standard-motion-plan.md").write_text(
                "Synthetic motion plan with explicit beats.\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "key-pose-concepts.png"):
                validate_design_gate_artifacts(project, load_contract(2))


if __name__ == "__main__":
    unittest.main()
