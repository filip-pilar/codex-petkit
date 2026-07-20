---
name: edit-pet
description: Make focused, reversible changes to an editable V2 Codex pet, including coherent look-direction repairs.
---

# Edit Pet

Change an existing V2 pet while preserving identity, unaffected frames, accepted history, and direction coherence.

## Boundaries

- Work inside this repository until the user explicitly asks to install or roll back.
- This pipeline supports only V2 packages and projects.
- Use `python3 -m petkit` for state, backups, edits, V2 builds, comparisons, reviews, installation, and rollback.
- Use `$imagegen` only when new pixels are required; read its current instructions first.
- Never modify an immutable build, backup, source project of a variant, or installed package in place.

## Locate and scope

Run `petkit status`. If the editable project predates V2, run the one-way `petkit upgrade-project`; this preserves its nine standard source rows and immutable history, then requires new V2 look production. If only an installed package exists, only a V2 package may be imported.

Before touching sources, identify the deterministic, generative, or linked-variant mode; exact allowed states; identity invariants; and baseline build. Record it with `petkit plan-edit`. Treat `look-a` and `look-b` as whole-row scopes: a direction cell is never an allowed standalone generative scope.

For a generative standard-row edit, renew the affected row's semantic design/capability evidence and key-pose review before building. A build cannot proceed without the project-local `qa/standard-motion-plan.md`, `qa/capability-audit.json`, `qa/key-pose-concepts.png`, and `qa/key-pose-review.json` gates.

## Deterministic edit

Use the smallest reversible operation: frame replacement/restoration, row re-extraction/restoration, safe running-left derivation, reconstruction, format, metadata, comparison, or transparent-pixel cleanup. Preserve every unaffected source byte. Individual frame replacement applies only to the nine standard animation rows; do not patch a registered look cell.

## Generative standard-row edit

Read [generative-editing.md](references/generative-editing.md), [semantic-design.md](references/semantic-design.md), [capability-audit.md](references/capability-audit.md), and [semantic-review.md](references/semantic-review.md). Ground `$imagegen` in the canonical identity, current row, semantic signature, capability audit, relevant original references, and a single observable delta. Generate and ingest only the affected standard row. Inspect its preview immediately. Retry deterministic extraction before regenerating visually correct art. An edit that makes a state less recognizable at full or UI size fails even when anatomy and pixels are technically valid.

## Generative look edit

Read [look-direction.md](references/look-direction.md). Preserve the recorded mechanics and approved cardinals.

- For `look-a`, regenerate all eight row-9 directions together, ingest the row, inspect it, and renew `approve-look-row-9`. Row 10 must then be regenerated or explicitly revalidated against the new row 9 before build.
- For `look-b`, regenerate all eight row-10 directions together, grounded in the canonical identity, cardinals, mechanics, and approved row 9.
- If cardinal semantics or the visual turn system changes, regenerate the cardinals and both rows in sequence.
- Never repair one direction cell, independently normalize directions, or treat 000 as neutral.
- If a complete coherent row has only a uniform measured scale mismatch, one documented `scale-look-row-source` x/y correction may be applied to the whole source row with one factor per axis; never correct poses independently.

## Linked variant

For a named alternate costume, palette, material, style, or recurring prop, use `petkit variant` first. Verify the new ID and parent link. The variant owns all later sources, builds, reviews, backups, and installation.

## Build and prove scope

Run `petkit build`. Inspect before/after, contact sheet, normal-size standard filmstrips, affected GIFs, direction sheets when applicable, validation, registration/despill reports, continuity, and change report. The builder rejects changed states outside the recorded scope.

Every new V2 build needs fresh direction evidence even when look pixels are unchanged: three context-isolated blind reviews using copies of the generated blind-verdict template and independent 16-direction semantics. It also needs exactly three prompt-blind visual-QA verdicts using private copies of the generated template. Visual reviewers receive canonical art, minimal state meanings, normal-size filmstrips, GIFs, and final sheets—not prompts, motion plans, prior verdicts, or answer keys—and must record the contract beat/support fields with distinct reviewer identifiers and pass unanimously. Record all three repeated `--independent-visual-qa` arguments with `petkit review-directions`.

Every new build also needs exactly three anonymous semantic-recognition verdicts. Reviewers classify randomized clips at full and approximate Codex UI size, reject calibration controls, and pass every required confusion pair without seeing row labels, prompts, motion plans, or answer keys. Each verdict must carry a distinct reviewer identifier. Record all three repeated `--semantic-verdict` arguments with `petkit review-directions`.

Read [review-gates.md](references/review-gates.md). Present the evidence and leave the build in review until the user accepts it. Then run `petkit accept` with explicit visual confirmation and a concrete note.

## Install or recover only on request

Use `petkit install --target-root ~/.codex/pets` only after explicit direction. Report the backup. Use `petkit rollback` only when requested, and report restored and displaced packages.

## Completion

Report mode, scope, baseline/new build, affected sources, before/after evidence, V2 direction review, exact unchanged states, reversibility paths, acceptance, installation, and backup status.
