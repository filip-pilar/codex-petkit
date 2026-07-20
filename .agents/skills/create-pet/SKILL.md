---
name: create-pet
description: Create a new high-quality editable V2 pet for the Codex desktop app, including coherent 16-direction looking.
---

# Create Pet

Create one new character identity and a complete V2 Codex pet project. Lead the creative process; the user should not have to manage atlas geometry, registration, chroma cleanup, or review plumbing.

## Boundaries

- Work inside this repository until the user explicitly asks to install an accepted package.
- Use `$imagegen` for every generative bitmap and read its current instructions immediately before generation.
- Use `python3 -m petkit` for project state, extraction, V2 assembly, validation, QA artifacts, history, installation, and rollback.
- Never synthesize production art with local drawing code, transforms, test fixtures, or placeholders.
- This pipeline supports V2 only: 1536×2288, 8×11, `spriteVersionNumber: 2`.
- Do not overwrite an existing project or installed pet.

## Start or resume

From the repository root, infer a stable slug, display name, description, and art direction when the request is sufficient. Ask only when a missing choice would materially change identity.

For an existing project, run `python3 -m petkit status --project pets/<id>` and resume at the first incomplete gate. For a new project, run `petkit init` with all supplied references.

## Identity gate

1. Record silhouette, proportions, face, palette, material, markings, props, personality, asymmetry, and avoidances in `identity.md`.
2. Generate a compact, padded, full-body canonical image on a flat project chroma background with `$imagegen`.
3. Copy every candidate into `references/candidates/`; never rely on the global generated-image copy.
4. Show the best candidate and require user approval before expensive row production.
5. Run `petkit approve-identity` and `petkit make-guides` after approval.

## Nine standard animation rows

Read [state-direction.md](references/state-direction.md), [state-distinction.md](references/state-distinction.md), [semantic-design.md](references/semantic-design.md), [capability-audit.md](references/capability-audit.md), and [animation-review.md](references/animation-review.md). Before generation, write `qa/standard-motion-plan.md`, `qa/capability-audit.json`, `qa/key-pose-concepts.png`, and `qa/key-pose-review.json`. The capability audit must contain one approved row-specific entry for all nine states naming emotional intent, observable meaning, primary full-size and thumbnail silhouette, face/eye cue, gaze target, rhythm, frame beats, motion amplitude, physical support/contact logic, capability or controlled prop used, and the states it must not resemble. The key-pose review must be independently judged at 192×208 and approximate UI size without prompt/motion-plan context. Include the required pairwise confusion matrix from the references. Do not begin full-strip generation until all four design-gate artifacts pass.

Generate one coherent strip per state, exactly matching the contract frame count. Ground each call in the canonical identity, its semantic signature, capability audit, motion-plan entry, and the layout guide only for pose count/spacing. Describe observable action before the internal state name. Repeat the row's positive signature and anti-overlap constraints in every repair prompt. If the action is not recognizable without the prompt, reject the concept instead of generating a subtler variant.

Inspect the generated source, ingest it with `petkit ingest-row`, and inspect both the normal-size filmstrip and `petkit preview-state` before continuing. Apply the rejection-first row gate in `animation-review.md`: examine every frame, every adjacent transition, and the last→first wrap. A row fails on one malformed limb, proportion/scale pop, material or lighting drift, purposeless frame, implausible support phase, crop, unclear action, or bad loop. Prompt intent never outweighs visible output. Repair only the failing row. Generate `running-left` independently unless markings, lighting, and props are genuinely mirror-safe; document any mirroring decision.

## Sixteen look directions

Read [look-direction.md](references/look-direction.md). This stage is sequential and its gates cannot be skipped.

1. Write an ordered mechanics JSON for all 16 directions. Each entry must include `degrees`, and explicit `eye`, `head`, and `body` cues. Record it with `petkit set-look-mechanics`.
2. Generate one coherent four-pose cardinal strip in this exact order: `000` up, `090` viewer screen-right, `180` down, `270` viewer screen-left. Preserve identity, scale, body anchor, lighting, asymmetry, and chroma background. Approve it with `petkit approve-cardinals` only after visual inspection.
3. Generate row 9 as one coherent eight-pose strip, ordered `000, 022.5, 045, 067.5, 090, 112.5, 135, 157.5`, grounded in the canonical identity, mechanics, and approved cardinals. Ingest it as `look-a`, inspect the complete row, then run `petkit approve-look-row-9`.
4. Only then generate row 10 as one coherent strip ordered `180, 202.5, 225, 247.5, 270, 292.5, 315, 337.5`, grounded in the canonical identity, approved cardinals, mechanics, and accepted row 9. Ingest it as `look-b`.

Never patch an individual direction cell. If one pose fails, regenerate its complete eight-pose row so interpolation remains coherent. `000` means up, never neutral. The builder registers both rows to the neutral frame and writes neutral to row 0 column 6. If repeated complete-row generations are coherent but uniformly miss the other row's measured scale, `petkit scale-look-row-source` may apply one documented uniform whole-row x/y correction; never use different factors per pose.

## Build and review

Run `petkit build`. The build must pass strict V2 validation and produces:

- contact sheet and all 11 GIF previews;
- direction QA sheet with head zooms;
- deterministic continuity report;
- randomized blind A/B sheet and a private answer key;
- randomized anonymous semantic-recognition sheets/GIFs at full and approximate Codex UI size, with calibration controls and a private state answer key;
- row-registration and one-pass despill reports;
- frame-level change report.

Perform three independent, context-isolated blind direction reviews using only the blind sheet—never disclose labels, generation prompts, or the answer key. Give each worker a private copy of `blind-verdict-template.json` and combine them by strict majority. A separate reviewer fills direction semantics.

Run three additional independent visual reviewers. Each receives only the canonical identity, state names with minimal contract meanings, normal-size filmstrips, animated previews at app timing, and the final contact/direction sheets. Never give them generation prompts, the motion plan, earlier verdicts, or claimed repairs. Each fills a private copy of the visual-QA template frame by frame, including the required contract beat/support fields, all adjacent transitions, and loop wraps. Use distinct reviewer identifiers. All three visual verdicts must pass unanimously; a majority is insufficient. Record the three `--independent-visual-qa` artifacts with `petkit review-directions`; continuity warnings need a concrete review note.

Run three additional anonymous semantic reviewers using [semantic-review.md](references/semantic-review.md). They receive randomized clip tokens, state meanings, full/UI-size assets, and calibration controls—not row labels or prompt context. Each must classify every clip correctly at both sizes, reject every calibration control, and pass every confusion pair. Use distinct reviewer identifiers. Record three `--semantic-verdict` artifacts with `petkit review-directions`; all three must pass independently.

Read [quality-gates.md](references/quality-gates.md). Regenerate a complete look row if semantics, anatomy, orientation, identity, continuity, or edges fail. Cardinals are a hard gate. Mechanical validation never substitutes for visual QA.

Acceptance records technical readiness, not proof of taste. Accept only after all review gates pass and the user positively approves the visible result:

```bash
python3 -m petkit accept --project pets/<id> --build-id <build-id> \
  --confirm-visual-qa --review-note "<observed result>"
```

## Install only on request

Install an accepted build only after explicit user direction with `petkit install --target-root ~/.codex/pets`. Report the replaced-package backup and the Codex refresh/check step. Never install skills globally.

## Completion

Report project and build paths, canonical identity, V2 validation, direction-review result, contact/direction sheets, relevant previews, repairs, acceptance, installation, and backup status.
