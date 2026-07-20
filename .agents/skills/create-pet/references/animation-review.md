# Rejection-first animation review

Judge visible output, never the prompt, motion plan, or the generator's apparent intent. A technically valid strip fails if it looks bad. Review the normal-size filmstrip and animated preview at app timing; neither is sufficient alone.

## Per-frame gate

Inspect every frame individually. Reject the complete row for any frame with:

- changed head-to-body ratio, limb thickness/length, eye construction, palette, material, or lighting direction;
- fused, duplicated, missing, bent, tangled, or ambiguous limbs or extremities;
- a pose that needs the prompt to explain it;
- accidental gesture language such as a dab, shrug, kneel, or T-pose unless that gesture is the intended state;
- clipping, edge crowding, inconsistent apparent scale, or unusable negative space;
- no distinct purpose in the action. Tiny eye or highlight changes do not make a useful animation frame.

Record the contract beat and visible support/contact evidence for every frame. The beat sequence is fixed: idle `rest → inhale → apex → exhale → settle → return`; directional runs `contact → compression → passing → flight → opposite-contact → compression → passing → flight-recovery`; waving `settled → lift → wave-accent → partial-return`; jumping `anticipation → lift → peak → descent → landing`; failed `startle → eyes-down → fold → bow → sad-hold → eye-lift → partial-rise → return`; waiting `attentive → extend → request → expectant-hold → hopeful-check → retract`; active work `focused-start → inward-pulse → processing-compression → open → second-pulse → open-return`; review `attentive-start → lean → inspection → scrutiny → reconsider → return`. A reviewer must reject a row when the visible frame cannot support its assigned beat.

## Transition and loop gate

Inspect every adjacent pair plus last→first. Reject for teleporting limbs, sudden facing changes, unsupported suspension, impossible contact/support changes, pose-order reversal, scale or color pops, or a wrap that visibly snaps. Running may contain a flight phase only when the surrounding contact and passing phases make it physically legible.

## Whole-row gate

At normal pet size and approximate Codex UI size, identify the action without reading its row label. Reject if the primary body motion is static, confusing, generic, or carried only by eyes. Compare against the canonical identity and every other standard row for proportions, material, palette, lighting, and apparent model scale. The separate anonymous semantic gate must classify the row correctly twice and must reject the calibration controls; a labeled reviewer explanation cannot override a failed anonymous result.

Frame counts are fixed by V2. Quality comes from making each frame a useful motion beat:

- `idle` (6): calm but visibly alive across the body; no near-static eye-only sequence.
- directional runs (8): readable contact, compression, passing, flight, opposite contact, and recovery; limb topology and support remain obvious.
- `waving` (4): all four frames contribute—settled, lift, clear wave accent, partial return. Frame 3 must not merely duplicate frame 0.
- `jumping` (5): anticipation changes posture, airborne frames retain the same model scale, and landing visibly absorbs impact. Vertical translation alone is not a jump.
- `failed` (8): emotional failure without resizing the head/body, accidental kneeling, or anatomical collapse. Sadness must come from controlled posture and eyes.
- `waiting` (6): readable user-directed expectancy with intact anatomy; avoid vague hand blobs or six near-copies.
- `running` work (6): a recognizable planted processing action, never locomotion, cross-body dabbing, random arm swapping, or palette drift.
- `review` (6): deliberate inspection with stable anatomy and a coherent slow loop, not a generic thinking pose repeated six times.

## Independence and verdict

Final visual reviewers may see canonical art, state names with minimal meanings, filmstrips, GIFs, and final QA sheets. They must not see prompts, the motion plan, prior reviews, or an answer key. Record anatomy and purpose for every frame, physical plausibility for every transition and loop wrap, seven quality gates per state, all anti-confusion pairs, and cross-state scale/proportion/material consistency.

One defect fails the row. Final readiness requires three independent unanimous passing visual verdicts with distinct reviewer identifiers. Regenerate the complete failing row; do not explain away or average out defects.
