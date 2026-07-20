from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from petkit.contract import Contract, State
from petkit.imageops import checkerboard, load_state_frames, _atomic_save
from petkit.project import atomic_write_json


SEMANTIC_REVIEW_VERSION = 1
SEMANTIC_THUMBNAIL_SCALE = 0.375
DESIGN_GATE_VERSION = 1

SEMANTIC_STATE_OPTIONS: dict[str, str] = {
    "idle": "calm resting life; quiet and self-contained",
    "running-right": "clear locomotion toward screen-right",
    "running-left": "clear locomotion toward screen-left",
    "waving": "friendly greeting or attention gesture",
    "jumping": "vertical jump with anticipation, lift, peak, descent, and landing",
    "failed": "sad, deflated, or error reaction",
    "waiting": "paused expectancy for approval, help, or user input",
    "running": "active task work or processing; not locomotion",
    "review": "slow focused inspection or evaluation",
}

SEMANTIC_CONFUSION_PAIRS = (
    ("idle", "waiting"),
    ("idle", "running"),
    ("idle", "review"),
    ("waiting", "running"),
    ("waiting", "review"),
    ("running", "review"),
    ("review", "failed"),
    ("failed", "idle"),
)


def validate_design_gate_artifacts(project_dir: Path, contract: Contract) -> None:
    """Require semantic design/capability evidence before a V2 build starts."""
    qa_dir = project_dir / "qa"
    motion_plan = qa_dir / "standard-motion-plan.md"
    if not motion_plan.is_file() or not motion_plan.read_text(encoding="utf-8").strip():
        raise ValueError("V2 build requires a non-empty qa/standard-motion-plan.md")
    concept_sheet = qa_dir / "key-pose-concepts.png"
    if not concept_sheet.is_file():
        raise ValueError("V2 build requires qa/key-pose-concepts.png before full-strip generation")

    expected_states = [state.id for state in contract.standard_states]
    capability_path = qa_dir / "capability-audit.json"
    try:
        capability = json.loads(capability_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError("V2 build requires qa/capability-audit.json") from exc
    except json.JSONDecodeError as exc:
        raise ValueError("qa/capability-audit.json is not valid JSON") from exc
    if capability.get("schema_version") != DESIGN_GATE_VERSION or capability.get("pass") is not True:
        raise ValueError("capability audit must be an approved V2 design gate")
    capability_entries = capability.get("states")
    if not isinstance(capability_entries, list) or [entry.get("state") for entry in capability_entries if isinstance(entry, dict)] != expected_states:
        raise ValueError("capability audit must cover all nine standard states in contract order")
    for entry in capability_entries:
        if entry.get("approved") is not True:
            raise ValueError(f"capability audit has not approved {entry.get('state')}")
        for field in ("capability", "thumbnail_cue", "anti_confusion"):
            if not isinstance(entry.get(field), str) or not entry[field].strip():
                raise ValueError(f"capability audit {entry.get('state')} requires {field} evidence")
        if entry["capability"].strip().lower() in {"eyes-only", "eye-only", "eyes only"}:
            raise ValueError(f"capability audit {entry.get('state')} cannot rely on an eye-only capability")

    key_pose_path = qa_dir / "key-pose-review.json"
    try:
        key_pose = json.loads(key_pose_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError("V2 build requires qa/key-pose-review.json") from exc
    except json.JSONDecodeError as exc:
        raise ValueError("qa/key-pose-review.json is not valid JSON") from exc
    if key_pose.get("schema_version") != DESIGN_GATE_VERSION or key_pose.get("pass") is not True:
        raise ValueError("key-pose concept review must be a passing V2 design gate")
    if not isinstance(key_pose.get("reviewer_id"), str) or not key_pose["reviewer_id"].strip():
        raise ValueError("key-pose concept review requires a reviewer identifier")
    if key_pose.get("reviewer_independent") is not True:
        raise ValueError("key-pose concept review must be independently judged")
    inputs = key_pose.get("review_inputs")
    if not isinstance(inputs, dict) or inputs.get("full_size_seen") is not True or inputs.get("thumbnail_size_seen") is not True or inputs.get("prompts_or_motion_plan_seen") is not False:
        raise ValueError("key-pose concept review must be full/UI-size and prompt-blind")
    pose_entries = key_pose.get("states")
    if not isinstance(pose_entries, list) or [entry.get("state") for entry in pose_entries if isinstance(entry, dict)] != expected_states:
        raise ValueError("key-pose concept review must cover all nine standard states in contract order")
    for entry in pose_entries:
        if entry.get("full_read") is not True or entry.get("thumbnail_read") is not True:
            raise ValueError(f"key-pose concept {entry.get('state')} is not readable at both sizes")
        if not isinstance(entry.get("note"), str) or not entry["note"].strip():
            raise ValueError(f"key-pose concept {entry.get('state')} requires recognition evidence")


def _token(index: int) -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyz"
    if index < len(alphabet):
        return f"clip-{alphabet[index]}"
    return f"clip-{index + 1:02d}"


def _calibration_token(index: int) -> str:
    return f"control-{index + 1:02d}"


def _render_strip(
    frames: list[Image.Image],
    label: str,
    output: Path,
    *,
    scale: float,
    cell_width: int,
    cell_height: int,
) -> None:
    width = len(frames) * round(cell_width * scale)
    height = 30 + round(cell_height * scale)
    sheet = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    draw.text((6, 8), label, fill="black", font=font)
    for index, frame in enumerate(frames):
        target_size = (round(cell_width * scale), round(cell_height * scale))
        resized = frame.resize(target_size, Image.Resampling.NEAREST)
        background = checkerboard(target_size, max(4, round(12 * scale)))
        background.paste(resized, (0, 0), resized)
        left = index * target_size[0]
        sheet.paste(background, (left, 30))
        draw.rectangle((left, 30, left + target_size[0] - 1, 30 + target_size[1] - 1), outline=(120, 120, 120))
        draw.rectangle((left + 3, 33, left + 20, 48), fill="white", outline=(80, 80, 80))
        draw.text((left + 8, 36), str(index), fill="black", font=font)
    output.parent.mkdir(parents=True, exist_ok=True)
    _atomic_save(sheet.convert("RGBA"), output, "PNG")


def _render_gif(
    frames: list[Image.Image],
    durations: tuple[int, ...],
    output: Path,
    *,
    scale: float,
    cell_width: int,
    cell_height: int,
) -> None:
    target_size = (round(cell_width * scale), round(cell_height * scale))
    rendered: list[Image.Image] = []
    for frame in frames:
        resized = frame.resize(target_size, Image.Resampling.NEAREST)
        background = checkerboard(target_size, max(4, round(12 * scale)))
        background.paste(resized, (0, 0), resized)
        rendered.append(background.convert("P", palette=Image.Palette.ADAPTIVE))
    output.parent.mkdir(parents=True, exist_ok=True)
    rendered[0].save(
        output,
        save_all=True,
        append_images=rendered[1:],
        duration=list(durations),
        loop=0,
        disposal=2,
        optimize=False,
    )


def _render_sheet(
    entries: list[tuple[str, list[Image.Image]]],
    output: Path,
    *,
    scale: float,
    cell_width: int,
    cell_height: int,
) -> None:
    target_width = max(len(frames) for _, frames in entries) * round(cell_width * scale)
    target_height = len(entries) * (30 + round(cell_height * scale))
    sheet = Image.new("RGB", (target_width, target_height), "white")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for row, (label, frames) in enumerate(entries):
        top = row * (30 + round(cell_height * scale))
        draw.text((6, top + 8), label, fill="black", font=font)
        for index, frame in enumerate(frames):
            target_size = (round(cell_width * scale), round(cell_height * scale))
            resized = frame.resize(target_size, Image.Resampling.NEAREST)
            background = checkerboard(target_size, max(4, round(12 * scale)))
            background.paste(resized, (0, 0), resized)
            left = index * target_size[0]
            sheet.paste(background, (left, top + 30))
            draw.rectangle((left, top + 30, left + target_size[0] - 1, top + 30 + target_size[1] - 1), outline=(120, 120, 120))
    output.parent.mkdir(parents=True, exist_ok=True)
    _atomic_save(sheet.convert("RGBA"), output, "PNG")


def _load_standard_frames(frames_root: Path, contract: Contract) -> dict[str, list[Image.Image]]:
    return {
        state.id: load_state_frames(frames_root, state, contract)
        for state in contract.standard_states
    }


def _calibration_frame_sets(
    frames_by_state: dict[str, list[Image.Image]],
    contract: Contract,
) -> list[tuple[str, list[Image.Image], tuple[int, ...], str]]:
    """Build deliberately bad, unlabeled reviewer-calibration controls.

    These are never production frames. They exercise the reviewer on the
    failure classes that a purely mechanical atlas check cannot establish:
    inert/repetitive motion, malformed/cropped anatomy, and identity/material
    drift.
    """
    idle = frames_by_state["idle"]
    waving = frames_by_state["waving"]
    jumping = frames_by_state["jumping"]
    running = frames_by_state["running"]
    static_idle = [idle[0].copy() for _ in idle]
    repeated_wave = [waving[0].copy() for _ in waving]
    cropped = jumping[2].copy()
    cropped.paste((0, 0, 0, 0), (0, 0, cropped.width, 24))
    cropped_jump = [cropped.copy() for _ in jumping]
    drift = running[0].copy()
    drift_pixels = drift.load()
    for y in range(drift.height):
        for x in range(drift.width):
            red, green, blue, alpha = drift_pixels[x, y]
            if alpha:
                drift_pixels[x, y] = (min(255, red + 90), max(0, green // 3), max(0, blue // 3), alpha)
    palette_drift = [drift.copy() for _ in running]
    return [
        ("idle", static_idle, contract.state("idle").durations_ms, "reject-as-inert"),
        ("waving", repeated_wave, contract.state("waving").durations_ms, "reject-as-repetitive"),
        ("jumping", cropped_jump, contract.state("jumping").durations_ms, "reject-as-cropped-or-malformed"),
        ("running", palette_drift, contract.state("running").durations_ms, "reject-as-identity-drift"),
    ]


def make_semantic_recognition_artifacts(
    frames_root: Path,
    output_dir: Path,
    private_dir: Path,
    contract: Contract,
    atlas_hash: str,
) -> dict[str, str]:
    """Create anonymous full/UI-size semantic-review assets and a private key.

    The visible artifacts contain only randomized clip tokens. State names and the
    token-to-state mapping live in the reviewer template/private answer key.
    """
    frames_by_state = _load_standard_frames(frames_root, contract)
    states = list(contract.standard_states)
    rng = random.Random(int(atlas_hash[:16], 16))
    rng.shuffle(states)
    entries: list[tuple[str, list[Image.Image]]] = []
    clips: list[dict[str, Any]] = []
    full_dir = output_dir / "full-filmstrips"
    thumb_dir = output_dir / "thumbnail-filmstrips"
    full_gif_dir = output_dir / "full-previews"
    thumb_gif_dir = output_dir / "thumbnail-previews"
    for index, state in enumerate(states):
        token = _token(index)
        frames = frames_by_state[state.id]
        full_strip = full_dir / f"{token}.png"
        thumb_strip = thumb_dir / f"{token}.png"
        full_gif = full_gif_dir / f"{token}.gif"
        thumb_gif = thumb_gif_dir / f"{token}.gif"
        _render_strip(frames, token, full_strip, scale=1.0, cell_width=contract.cell_width, cell_height=contract.cell_height)
        _render_strip(frames, token, thumb_strip, scale=SEMANTIC_THUMBNAIL_SCALE, cell_width=contract.cell_width, cell_height=contract.cell_height)
        _render_gif(frames, state.durations_ms, full_gif, scale=1.0, cell_width=contract.cell_width, cell_height=contract.cell_height)
        _render_gif(frames, state.durations_ms, thumb_gif, scale=SEMANTIC_THUMBNAIL_SCALE, cell_width=contract.cell_width, cell_height=contract.cell_height)
        entries.append((token, frames))
        clips.append(
            {
                "token": token,
                "full_filmstrip": str(full_strip.relative_to(output_dir)),
                "thumbnail_filmstrip": str(thumb_strip.relative_to(output_dir)),
                "full_preview": str(full_gif.relative_to(output_dir)),
                "thumbnail_preview": str(thumb_gif.relative_to(output_dir)),
            }
        )
    full_sheet = output_dir / "semantic-full-sheet.png"
    thumbnail_sheet = output_dir / "semantic-thumbnail-sheet.png"
    _render_sheet(entries, full_sheet, scale=1.0, cell_width=contract.cell_width, cell_height=contract.cell_height)
    _render_sheet(entries, thumbnail_sheet, scale=SEMANTIC_THUMBNAIL_SCALE, cell_width=contract.cell_width, cell_height=contract.cell_height)

    controls_dir = output_dir / "calibration"
    controls: list[dict[str, str]] = []
    for index, (_state_id, control_frames, durations, expected) in enumerate(_calibration_frame_sets(frames_by_state, contract)):
        token = _calibration_token(index)
        strip = controls_dir / f"{token}.png"
        preview = controls_dir / f"{token}.gif"
        _render_strip(control_frames, token, strip, scale=SEMANTIC_THUMBNAIL_SCALE, cell_width=contract.cell_width, cell_height=contract.cell_height)
        _render_gif(control_frames, durations, preview, scale=SEMANTIC_THUMBNAIL_SCALE, cell_width=contract.cell_width, cell_height=contract.cell_height)
        controls.append(
            {
                "id": token,
                "filmstrip": str(strip.relative_to(output_dir)),
                "preview": str(preview.relative_to(output_dir)),
                "expected": expected,
            }
        )

    answer_key = private_dir / "semantic-recognition-answer-key.json"
    atomic_write_json(
        answer_key,
        {
            "schema_version": SEMANTIC_REVIEW_VERSION,
            "atlas_sha256": atlas_hash,
            "clips": [{"token": clip["token"], "state": state.id} for clip, state in zip(clips, states)],
            "controls": controls,
        },
    )
    manifest = {
        "schema_version": SEMANTIC_REVIEW_VERSION,
        "atlas_sha256": atlas_hash,
        "state_options": SEMANTIC_STATE_OPTIONS,
        "clips": clips,
        "full_sheet": str(full_sheet.relative_to(output_dir)),
        "thumbnail_sheet": str(thumbnail_sheet.relative_to(output_dir)),
        "calibration": controls,
        "answer_key": str(answer_key.relative_to(private_dir)),
    }
    manifest_path = output_dir / "semantic-manifest.json"
    atomic_write_json(manifest_path, manifest)
    template = {
        "schema_version": SEMANTIC_REVIEW_VERSION,
        "atlas_sha256": atlas_hash,
        "reviewer_id": "replace with the independent reviewer's identifier",
        "reviewer_independent": True,
        "pass": False,
        "note": "replace with anonymous recognition evidence",
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
                "full_state": "replace with observed state id",
                "thumbnail_state": "replace with observed state id",
                "full_alternative": "replace with strongest alternative or none",
                "thumbnail_alternative": "replace with strongest alternative or none",
                "full_evidence": "",
                "thumbnail_evidence": "",
            }
            for clip in clips
        ],
        "pairwise_confusions": [
            {
                "states": list(pair),
                "full_distinct": False,
                "thumbnail_distinct": False,
                "evidence": "replace with anonymous comparison evidence",
            }
            for pair in SEMANTIC_CONFUSION_PAIRS
        ],
        "calibration": [
            {"id": control["id"], "result": "replace with reject", "evidence": ""}
            for control in controls
        ],
    }
    template_path = output_dir / "semantic-recognition-template.json"
    atomic_write_json(template_path, template)
    return {
        "manifest": str(manifest_path),
        "template": str(template_path),
        "full_sheet": str(full_sheet),
        "thumbnail_sheet": str(thumbnail_sheet),
        "answer_key": str(answer_key),
    }
