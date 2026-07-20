from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

from petkit.contract import Contract
from petkit.project import atomic_write_json, sha256_file
from petkit.semantic import (
    SEMANTIC_CONFUSION_PAIRS,
    SEMANTIC_REVIEW_VERSION,
    SEMANTIC_STATE_OPTIONS,
)


STANDARD_STATE_IDS = (
    "idle",
    "running-right",
    "running-left",
    "waving",
    "jumping",
    "failed",
    "waiting",
    "running",
    "review",
)
STANDARD_CONFUSION_PAIRS = (
    ("idle", "waiting"),
    ("waiting", "running"),
    ("running", "review"),
    ("review", "failed"),
    ("failed", "idle"),
)
STANDARD_FRAME_COUNTS = {
    "idle": 6,
    "running-right": 8,
    "running-left": 8,
    "waving": 4,
    "jumping": 5,
    "failed": 8,
    "waiting": 6,
    "running": 6,
    "review": 6,
}
STANDARD_FRAME_BEATS = {
    "idle": ("rest", "inhale", "apex", "exhale", "settle", "return"),
    "running-right": ("contact", "compression", "passing", "flight", "opposite-contact", "compression", "passing", "flight-recovery"),
    "running-left": ("contact", "compression", "passing", "flight", "opposite-contact", "compression", "passing", "flight-recovery"),
    "waving": ("settled", "lift", "wave-accent", "partial-return"),
    "jumping": ("anticipation", "lift", "peak", "descent", "landing"),
    "failed": ("startle", "eyes-down", "fold", "bow", "sad-hold", "eye-lift", "partial-rise", "return"),
    "waiting": ("attentive", "extend", "request", "expectant-hold", "hopeful-check", "retract"),
    "running": ("focused-start", "inward-pulse", "processing-compression", "open", "second-pulse", "open-return"),
    "review": ("attentive-start", "lean", "inspection", "scrutiny", "reconsider", "return"),
}
STANDARD_QUALITY_GATES = (
    "anatomy_topology",
    "identity_material",
    "scale_proportion",
    "framing_readability",
    "motion_coherence",
    "loop_quality",
    "frame_contribution",
)
CROSS_STATE_QUALITY_GATES = (
    "apparent_scale",
    "head_body_proportion",
    "palette_material_lighting",
)


SCRIPT_ROOT = Path(__file__).resolve().parent / "v2scripts"


def run_script(name: str, arguments: Iterable[str | Path], *, allow_failure: bool = False) -> subprocess.CompletedProcess[str]:
    script = SCRIPT_ROOT / name
    command = [sys.executable, str(script), *(str(value) for value in arguments)]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode and not allow_failure:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit code {completed.returncode}"
        raise ValueError(f"V2 helper {name} failed: {detail}")
    return completed


def assemble_v2(
    *,
    base_atlas: Path,
    look_row_9: Path,
    look_row_10: Path,
    output_dir: Path,
    chroma_key: str,
    chroma_threshold: float,
) -> dict[str, str]:
    registered = output_dir / "registered-look-row-9.png"
    registration = output_dir / "look-registration.json"
    raw = output_dir / "spritesheet-before-despill.png"
    png = output_dir / "spritesheet.png"
    webp = output_dir / "spritesheet.webp"
    despill = output_dir / "despill.json"
    run_script(
        "assemble_extended_atlas.py",
        (
            "--base-atlas", base_atlas,
            "--look-row-9", look_row_9,
            "--registered-row-output", registered,
            "--registration-manifest-output", registration,
            "--chroma-key", chroma_key,
            "--chroma-threshold", str(chroma_threshold),
        ),
    )
    run_script(
        "assemble_extended_atlas.py",
        (
            "--base-atlas", base_atlas,
            "--registered-row-9", registered,
            "--look-row-10", look_row_10,
            "--row-9-registration", registration,
            "--output", raw,
            "--chroma-key", chroma_key,
            "--chroma-threshold", str(chroma_threshold),
        ),
    )
    run_script(
        "despill_chroma_edges.py",
        (
            raw,
            "--output", png,
            "--webp-output", webp,
            "--json-out", despill,
            "--chroma-key", chroma_key,
        ),
    )
    return {
        "registered_row_9": str(registered),
        "registration": str(registration),
        "raw_atlas": str(raw),
        "png": str(png),
        "webp": str(webp),
        "despill": str(despill),
    }


def validate_v2(atlas: Path, output: Path, *, chroma_key: str) -> dict[str, Any]:
    completed = run_script(
        "validate_atlas.py",
        (atlas, "--json-out", output, "--require-v2", "--chroma-key", chroma_key),
        allow_failure=True,
    )
    if not output.is_file():
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ValueError(f"V2 validator did not write a report: {detail}")
    return json.loads(output.read_text(encoding="utf-8"))


def make_direction_artifacts(atlas: Path, public_dir: Path, private_dir: Path) -> dict[str, str]:
    public_dir.mkdir(parents=True, exist_ok=True)
    private_dir.mkdir(parents=True, exist_ok=True)
    direction_sheet = public_dir / "direction-qa.png"
    blind_sheet = public_dir / "direction-blind.png"
    answer_key = private_dir / "direction-blind-answer-key.json"
    continuity = public_dir / "direction-continuity.json"
    run_script("make_direction_qa_sheet.py", (atlas, "--output", direction_sheet))
    run_script(
        "make_direction_blind_qa_sheet.py",
        (atlas, "--output", blind_sheet, "--answer-key", answer_key),
    )
    run_script("measure_direction_continuity.py", (atlas, "--json-out", continuity))
    atlas_hash = sha256_file(atlas)
    answer = json.loads(answer_key.read_text(encoding="utf-8"))
    blind_template = public_dir / "blind-verdict-template.json"
    semantics_template = public_dir / "direction-semantics-template.json"
    visual_template = public_dir / "independent-visual-qa-template.json"
    atomic_write_json(
        blind_template,
        {
            "reviewer_independent": True,
            "pairs": [
                {"pair": pair["pair"], "A": "ambiguous", "B": "ambiguous", "reason": "replace with blind observation"}
                for pair in answer["pairs"]
            ],
        },
    )
    atomic_write_json(
        semantics_template,
        {
            "atlas_sha256": atlas_hash,
            "reviewer_independent": True,
            "directions": [
                {"degrees": value, "observed": "replace with observed screen direction", "pass": False, "note": ""}
                for value in (0, 22.5, 45, 67.5, 90, 112.5, 135, 157.5, 180, 202.5, 225, 247.5, 270, 292.5, 315, 337.5)
            ],
        },
    )
    atomic_write_json(
        visual_template,
        {
            "atlas_sha256": atlas_hash,
            "reviewer_id": "replace with the independent reviewer's identifier",
            "reviewer_independent": True,
            "pass": False,
            "note": "replace with identity, anatomy, edge, registration, and app-fitness observations",
            "review_inputs": {
                "canonical_identity_seen": True,
                "normal_size_filmstrips_seen": True,
                "animated_previews_seen": True,
                "prompts_or_motion_plan_seen": False,
            },
            "standard_states": [
                {
                    "state": state,
                    "observed_action": "replace with the visible normal-size action",
                    "silhouette_signature": "replace with the primary visible silhouette cue",
                    "frame_observations": [
                        {
                            "index": index,
                            "beat": STANDARD_FRAME_BEATS[state][index],
                            "support": "replace with visible support/contact evidence",
                            "anatomy": "replace with observed limb/head/body integrity",
                            "contribution": "replace with this frame's distinct purpose in the action",
                        }
                        for index in range(STANDARD_FRAME_COUNTS[state])
                    ],
                    "transition_observations": [
                        {
                            "from": index,
                            "to": (index + 1) % STANDARD_FRAME_COUNTS[state],
                            "plausible": False,
                            "note": "replace with observed physical continuity, including the loop wrap",
                        }
                        for index in range(STANDARD_FRAME_COUNTS[state])
                    ],
                    "quality_gates": {
                        gate: {"pass": False, "note": f"replace with {gate} evidence"}
                        for gate in STANDARD_QUALITY_GATES
                    },
                    "pass": False,
                    "note": "",
                }
                for state in STANDARD_STATE_IDS
            ],
            "confusion_pairs": [
                {
                    "states": list(pair),
                    "distinct": False,
                    "evidence": "replace with normal-size silhouette and rhythm evidence",
                }
                for pair in STANDARD_CONFUSION_PAIRS
            ],
            "cross_state_consistency": {
                gate: {"pass": False, "note": f"replace with cross-state {gate} evidence"}
                for gate in CROSS_STATE_QUALITY_GATES
            },
        },
    )
    return {
        "direction_sheet": str(direction_sheet),
        "blind_sheet": str(blind_sheet),
        "answer_key": str(answer_key),
        "continuity": str(continuity),
        "blind_verdict_template": str(blind_template),
        "direction_semantics_template": str(semantics_template),
        "independent_visual_qa_template": str(visual_template),
    }


def validate_mechanics(payload: dict[str, Any], contract: Contract) -> None:
    entries = payload.get("directions")
    if not isinstance(entries, list) or len(entries) != 16:
        raise ValueError("look mechanics must define exactly 16 ordered directions")
    observed: list[float] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"look mechanics direction {index} must be an object")
        try:
            observed.append(float(entry["degrees"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"look mechanics direction {index} has invalid degrees") from exc
        for field in ("eye", "head", "body"):
            if not isinstance(entry.get(field), str) or not entry[field].strip():
                raise ValueError(f"look mechanics direction {entry.get('degrees')} requires a non-empty {field} cue")
    if tuple(observed) != contract.look_directions_degrees:
        raise ValueError("look mechanics directions are missing or out of V2 order")


def validate_direction_semantics(payload: dict[str, Any], contract: Contract, atlas_hash: str) -> None:
    if payload.get("atlas_sha256") != atlas_hash:
        raise ValueError("direction-semantics review does not match this atlas")
    if payload.get("reviewer_independent") is not True:
        raise ValueError("direction-semantics review must be marked reviewer_independent")
    entries = payload.get("directions")
    if not isinstance(entries, list) or len(entries) != 16:
        raise ValueError("direction-semantics review must classify all 16 directions")
    observed = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("direction-semantics entries must be objects")
        observed.append(float(entry.get("degrees", -1)))
        if entry.get("pass") is not True:
            raise ValueError(f"direction {entry.get('degrees')} did not pass semantic review")
        if not isinstance(entry.get("observed"), str) or not entry["observed"].strip():
            raise ValueError(f"direction {entry.get('degrees')} is missing an observed direction")
    if tuple(observed) != contract.look_directions_degrees:
        raise ValueError("direction-semantics entries are out of V2 order")


def validate_visual_qa(payload: dict[str, Any], atlas_hash: str) -> None:
    if payload.get("atlas_sha256") != atlas_hash:
        raise ValueError("independent visual QA does not match this atlas")
    if payload.get("reviewer_independent") is not True or payload.get("pass") is not True:
        raise ValueError("independent visual QA must be independent and passing")
    if not isinstance(payload.get("note"), str) or not payload["note"].strip():
        raise ValueError("independent visual QA requires a review note")
    if not isinstance(payload.get("reviewer_id"), str) or not payload["reviewer_id"].strip():
        raise ValueError("independent visual QA requires a reviewer identifier")
    inputs = payload.get("review_inputs")
    expected_inputs = {
        "canonical_identity_seen": True,
        "normal_size_filmstrips_seen": True,
        "animated_previews_seen": True,
        "prompts_or_motion_plan_seen": False,
    }
    if not isinstance(inputs, dict) or any(inputs.get(key) is not value for key, value in expected_inputs.items()):
        raise ValueError("independent visual QA must inspect canonical art, normal-size filmstrips, and animations without prompt or motion-plan leakage")
    states = payload.get("standard_states")
    if not isinstance(states, list) or len(states) != len(STANDARD_STATE_IDS):
        raise ValueError("independent visual QA must review all nine standard states")
    observed_states = []
    for entry in states:
        if not isinstance(entry, dict):
            raise ValueError("standard-state QA entries must be objects")
        observed_states.append(entry.get("state"))
        if entry.get("pass") is not True:
            raise ValueError(f"standard state {entry.get('state')} did not pass visual QA")
        for field in ("observed_action", "silhouette_signature", "note"):
            if not isinstance(entry.get(field), str) or not entry[field].strip():
                raise ValueError(f"standard state {entry.get('state')} requires a non-empty {field}")
        frame_count = STANDARD_FRAME_COUNTS.get(str(entry.get("state")))
        frame_observations = entry.get("frame_observations")
        if frame_count is None or not isinstance(frame_observations, list) or len(frame_observations) != frame_count:
            raise ValueError(f"standard state {entry.get('state')} requires one observation per frame")
        for index, observation in enumerate(frame_observations):
            if not isinstance(observation, dict) or observation.get("index") != index:
                raise ValueError(f"standard state {entry.get('state')} frame observations are missing or out of order")
            expected_beat = STANDARD_FRAME_BEATS[str(entry.get("state"))][index]
            if observation.get("beat") != expected_beat:
                raise ValueError(f"standard state {entry.get('state')} frame {index} requires beat {expected_beat}")
            for field in ("support", "anatomy", "contribution"):
                if not isinstance(observation.get(field), str) or not observation[field].strip():
                    raise ValueError(f"standard state {entry.get('state')} frame {index} requires a non-empty {field} observation")
        transitions = entry.get("transition_observations")
        if not isinstance(transitions, list) or len(transitions) != frame_count:
            raise ValueError(f"standard state {entry.get('state')} requires every adjacent transition and loop wrap to be reviewed")
        for index, transition in enumerate(transitions):
            expected_to = (index + 1) % frame_count
            if not isinstance(transition, dict) or transition.get("from") != index or transition.get("to") != expected_to:
                raise ValueError(f"standard state {entry.get('state')} transitions are missing or out of order")
            if transition.get("plausible") is not True:
                raise ValueError(f"standard state {entry.get('state')} transition {index}->{expected_to} is not physically coherent")
            if not isinstance(transition.get("note"), str) or not transition["note"].strip():
                raise ValueError(f"standard state {entry.get('state')} transition {index}->{expected_to} requires evidence")
        gates = entry.get("quality_gates")
        if not isinstance(gates, dict) or set(gates) != set(STANDARD_QUALITY_GATES):
            raise ValueError(f"standard state {entry.get('state')} requires every frame-quality gate")
        for gate in STANDARD_QUALITY_GATES:
            result = gates[gate]
            if not isinstance(result, dict) or result.get("pass") is not True:
                raise ValueError(f"standard state {entry.get('state')} failed {gate} QA")
            if not isinstance(result.get("note"), str) or not result["note"].strip():
                raise ValueError(f"standard state {entry.get('state')} requires {gate} evidence")
    if tuple(observed_states) != STANDARD_STATE_IDS:
        raise ValueError("standard-state QA entries are missing or out of contract order")
    pairs = payload.get("confusion_pairs")
    if not isinstance(pairs, list) or len(pairs) != len(STANDARD_CONFUSION_PAIRS):
        raise ValueError("independent visual QA must review every required state-confusion pair")
    observed_pairs = []
    for entry in pairs:
        if not isinstance(entry, dict) or not isinstance(entry.get("states"), list):
            raise ValueError("state-confusion QA entries must be objects with a states pair")
        observed_pairs.append(tuple(entry["states"]))
        if entry.get("distinct") is not True:
            raise ValueError(f"standard states {entry.get('states')} are not visually distinct")
        if not isinstance(entry.get("evidence"), str) or not entry["evidence"].strip():
            raise ValueError(f"standard states {entry.get('states')} require distinction evidence")
    if tuple(observed_pairs) != STANDARD_CONFUSION_PAIRS:
        raise ValueError("state-confusion QA pairs are missing or out of required order")
    consistency = payload.get("cross_state_consistency")
    if not isinstance(consistency, dict) or set(consistency) != set(CROSS_STATE_QUALITY_GATES):
        raise ValueError("independent visual QA requires all cross-state consistency gates")
    for gate in CROSS_STATE_QUALITY_GATES:
        result = consistency[gate]
        if not isinstance(result, dict) or result.get("pass") is not True:
            raise ValueError(f"independent visual QA failed cross-state {gate}")
        if not isinstance(result.get("note"), str) or not result["note"].strip():
            raise ValueError(f"independent visual QA requires cross-state {gate} evidence")


def validate_semantic_recognition(
    payload: dict[str, Any],
    answer_key: dict[str, Any],
    atlas_hash: str,
) -> None:
    """Validate one anonymous, full-size and UI-size state-recognition verdict."""
    if payload.get("schema_version") != SEMANTIC_REVIEW_VERSION:
        raise ValueError("semantic recognition verdict has an unsupported schema version")
    if answer_key.get("schema_version") != SEMANTIC_REVIEW_VERSION:
        raise ValueError("semantic recognition answer key has an unsupported schema version")
    if payload.get("atlas_sha256") != atlas_hash or answer_key.get("atlas_sha256") != atlas_hash:
        raise ValueError("semantic recognition verdict does not match this atlas")
    if payload.get("reviewer_independent") is not True or payload.get("pass") is not True:
        raise ValueError("semantic recognition verdict must be independent and passing")
    if not isinstance(payload.get("reviewer_id"), str) or not payload["reviewer_id"].strip():
        raise ValueError("semantic recognition requires a reviewer identifier")
    if not isinstance(payload.get("note"), str) or not payload["note"].strip():
        raise ValueError("semantic recognition verdict requires a review note")
    expected_inputs = {
        "canonical_identity_seen": True,
        "semantic_full_sheet_seen": True,
        "semantic_thumbnail_sheet_seen": True,
        "semantic_full_previews_seen": True,
        "semantic_thumbnail_previews_seen": True,
        "calibration_controls_seen": True,
        "prompts_or_motion_plan_seen": False,
    }
    inputs = payload.get("review_inputs")
    if not isinstance(inputs, dict) or any(inputs.get(key) is not value for key, value in expected_inputs.items()):
        raise ValueError("semantic recognition must be anonymous, full-size, UI-size, and calibration-aware")
    if payload.get("state_options") != SEMANTIC_STATE_OPTIONS:
        raise ValueError("semantic recognition state options do not match the V2 contract")

    expected_clips = answer_key.get("clips")
    if not isinstance(expected_clips, list) or not expected_clips:
        raise ValueError("semantic recognition answer key has no clips")
    expected_by_token = {entry.get("token"): entry.get("state") for entry in expected_clips if isinstance(entry, dict)}
    assignments = payload.get("assignments")
    assignment_tokens = [entry.get("token") for entry in assignments if isinstance(entry, dict)] if isinstance(assignments, list) else []
    if (
        not isinstance(assignments, list)
        or len(assignments) != len(expected_by_token)
        or len(assignment_tokens) != len(set(assignment_tokens))
        or set(assignment_tokens) != set(expected_by_token)
    ):
        raise ValueError("semantic recognition must classify every anonymous clip exactly once")
    for entry in assignments:
        if not isinstance(entry, dict):
            raise ValueError("semantic recognition assignments must be objects")
        token = entry.get("token")
        expected_state = expected_by_token.get(token)
        for view in ("full", "thumbnail"):
            state = entry.get(f"{view}_state")
            if state not in SEMANTIC_STATE_OPTIONS:
                raise ValueError(f"semantic recognition {token} has an invalid {view} classification")
            if state != expected_state:
                raise ValueError(f"semantic recognition {token} is misclassified at {view} size")
            alternative = entry.get(f"{view}_alternative")
            evidence = entry.get(f"{view}_evidence")
            if not isinstance(alternative, str) or not alternative.strip():
                raise ValueError(f"semantic recognition {token} requires a strongest {view}-size alternative")
            if not isinstance(evidence, str) or not evidence.strip():
                raise ValueError(f"semantic recognition {token} requires {view}-size evidence")

    pairs = payload.get("pairwise_confusions")
    if not isinstance(pairs, list) or [tuple(entry.get("states", [])) for entry in pairs if isinstance(entry, dict)] != list(SEMANTIC_CONFUSION_PAIRS):
        raise ValueError("semantic recognition pairwise confusion coverage is incomplete or out of order")
    for pair in pairs:
        if pair.get("full_distinct") is not True or pair.get("thumbnail_distinct") is not True:
            raise ValueError(f"semantic recognition pair {pair.get('states')} is not distinct at both sizes")
        if not isinstance(pair.get("evidence"), str) or not pair["evidence"].strip():
            raise ValueError(f"semantic recognition pair {pair.get('states')} requires evidence")

    expected_controls = answer_key.get("controls")
    calibration = payload.get("calibration")
    if not isinstance(expected_controls, list) or not isinstance(calibration, list):
        raise ValueError("semantic recognition requires calibration controls")
    expected_control_ids = [entry.get("id") for entry in expected_controls if isinstance(entry, dict)]
    if [entry.get("id") for entry in calibration if isinstance(entry, dict)] != expected_control_ids:
        raise ValueError("semantic calibration controls are missing or out of order")
    for control in calibration:
        if control.get("result") not in {"reject", "rejected"}:
            raise ValueError(f"semantic calibration control {control.get('id')} was not rejected")
        if not isinstance(control.get("evidence"), str) or not control["evidence"].strip():
            raise ValueError(f"semantic calibration control {control.get('id')} requires evidence")


def combine_and_validate_blind_reviews(
    *,
    answer_key: Path,
    verdicts: list[Path],
    output_dir: Path,
) -> dict[str, Any]:
    if len(verdicts) < 3 or len(verdicts) % 2 == 0:
        raise ValueError("direction QA requires an odd number of at least three independent blind verdicts")
    output_dir.mkdir(parents=True, exist_ok=True)
    copied = []
    for index, source in enumerate(verdicts, start=1):
        target = output_dir / f"blind-verdict-{index:02d}.json"
        shutil.copy2(source, target)
        copied.append(target)
    combined = output_dir / "blind-verdict-combined.json"
    arguments: list[str | Path] = []
    for path in copied:
        arguments.extend(("--verdicts", path))
    arguments.extend(("--json-out", combined))
    run_script("combine_direction_blind_verdicts.py", arguments)
    validation = output_dir / "blind-verdict-validation.json"
    run_script(
        "validate_direction_blind_verdicts.py",
        ("--answer-key", answer_key, "--verdicts", combined, "--json-out", validation),
    )
    result = json.loads(validation.read_text(encoding="utf-8"))
    if not result.get("ok"):
        raise ValueError("combined blind direction review did not pass")
    return {"validation": result, "combined": str(combined), "verdicts": [str(path) for path in copied]}
