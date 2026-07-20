# V2 quality gates

## Mechanical

- Lossless WebP at exactly 1536×2288, 8×11 cells of 192×208.
- `pet.json` contains `spriteVersionNumber: 2`.
- Nine standard rows plus 16 used look cells; row 0 column 6 contains the neutral/default frame.
- Used cells are visible, unused cells transparent, no transparent RGB residue, chroma leak, or chroma fringe.
- The completed atlas receives exactly one edge-local despill pass.
- Registered look rows share scale and lower-body/baseline anchoring with neutral.

## Direction semantics

- Ordered clockwise: 000 up, 090 screen-right, 180 down, 270 screen-left.
- All 16 classifications pass independent labeled review.
- Three context-isolated blind A/B reviews reach a strict per-cell majority; cardinal pairs are hard gates.
- Continuity report warnings are visually resolved or explicitly reviewed.
- No complete eight-pose look row is patched cell-by-cell.

## Identity and motion

- Silhouette, proportions, face, markings, palette, materials, lighting, asymmetry, and props remain stable.
- Look poses rotate gaze/head/body coherently without facial mutation, eye duplication, detached features, or size/baseline pops.
- Standard previews remain readable, loopable, unclipped, state-correct, and non-static. Every frame and every adjacent transition, including last→first, passes the rejection-first animation review.
- Exact duplicate frames and final-frame/first-frame duplicates are hard failures for standard rows; a held timing value belongs in the contract duration, not in copied frame pixels. Standard-frame edge contact is a hard failure, not a warning.
- Limb topology, head/body proportion, apparent model scale, palette, material, and lighting remain stable frame to frame and across rows. One malformed or ambiguous frame fails its complete row.
- Every frame contributes a distinct physical beat. Eye-only differences, near-copies, accidental gestures, unsupported suspension, and prompt-dependent interpretations fail.
- Reduced-motion idle frame, waiting, working, review, and failed remain distinct at cell size.
- Three prompt-blind independent final reviewers with distinct reviewer identifiers unanimously record the visible action, the contract beat and support evidence for every frame, every frame's anatomy and contribution, every transition's physical continuity, and seven quality dimensions for all nine standard rows.
- The required confusion pairs all pass at normal pet size: idle/waiting, waiting/running-work, running-work/review, review/failed, and failed/idle.
- Three independent anonymous semantic reviewers classify randomized clips correctly at both full and approximate Codex UI size, reject every inert, repetitive, cropped/malformed, or identity-drift calibration control, and pass the complete semantic confusion set without seeing prompts, plans, row labels, prior verdicts, or answer keys. A majority is insufficient.
- Semantic concepts pass before full-strip generation; a capability audit proves each abstract state has a readable body, appendage, face, or controlled-prop cue at thumbnail size.
- Apparent character scale and planted baseline remain consistent across standard rows; expressive amplitude changes posture and silhouette without making the character look like a differently sized model.

## Repair order

1. Deterministic re-extraction when the source is visually correct.
2. One-frame deterministic replacement only for standard animation rows.
3. Complete-row grounded regeneration for a failing standard or look row.
4. Canonical identity revision only for broad drift and only after user approval.

After repair, compare builds and prove every out-of-scope state stayed frame-identical.
