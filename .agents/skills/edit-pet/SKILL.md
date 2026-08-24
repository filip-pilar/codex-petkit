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

Run `petkit status` and follow its phase-specific blockers rather than inferring readiness from the status label. PetKit serializes mutating commands per project; do not edit project sources manually while one is running. A build consumes a verified private input snapshot and aborts if live authority drifts before publication. If a build, identity approval, or variant command is cancelled, allow it to finish transaction recovery before retrying; the next mutation also reconciles any durable `.petkit-recovery` marker. A cancellation note identifies a verified release, candidate, identity, or child variant that was already durably committed, while a transaction-recovery error names every stable path that still needs attention. Do not delete a reported preapproval identity path: it can contain the only prior canonical bytes needed by the next reconciliation attempt. If the editable project predates V2, run the one-way `petkit upgrade-project`; this preserves its nine standard source rows and immutable history, archives the pre-V2 accepted pointer, and requires new V2 look production plus a fresh V2 accepted baseline. If an older V2 baseline predates integrity binding, the same command archives that accepted pointer, clears its stale cardinal/row approvals, and starts a fresh reviewed V2 baseline. For an unaccepted legacy variant missing or predating the schema-2 fork snapshot, invoke `upgrade-project` only when its retained current sources and authority are ready to become the explicit owner-approved rebaseline; the command deliberately does not overwrite a valid schema-2 snapshot when the fork has drifted. If only an installed package exists, only a V2 package may be imported. An import is a repairable recovery bootstrap, not an unchanged fork or approved project: repair recovered pixels if necessary, complete its normal local gates, then build, review, and accept a project-local baseline before `plan-edit`.

Before touching sources, read [edit-modes.md](references/edit-modes.md), then identify the deterministic, generative, or linked-variant mode; exact allowed states; identity invariants; and baseline build. Record it with `petkit plan-edit`. Treat `look-a` and `look-b` as whole-row scopes: a direction cell is never an allowed standalone generative scope.

After an accepted baseline, changing the canonical identity invalidates its look approvals and review evidence. Prefer a new project or linked variant for that identity change. If it must remain in the same project, scope every state and complete fresh look approval, build, and review evidence.

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

For a named alternate costume, palette, material, style, or recurring prop, first ensure the parent has a current accepted release and no active edit, then use `petkit variant`. Variant creation revalidates the accepted build, review, acceptance, live source, authority, and build parameters before copying a private verified snapshot; repair any reported parent drift instead of bypassing it. Verify the new ID and parent link. The variant owns all later sources, builds, reviews, backups, and installation. Before changing the fork, build, review, and accept its unchanged sources as a child-local baseline; a parent build cannot serve as the variant's scoped-edit baseline.

## Build and prove scope

While iterating on a repair, use `petkit build --draft` to inspect a mechanically validated candidate quickly. It keeps the accepted/current release untouched, reuses only hash-verified unaffected previews, filmstrips, and despill cells, and cannot be reviewed, accepted, or installed. Once the repair is ready, run `petkit build` to create the immutable release. Inspect before/after, contact sheet, normal-size standard filmstrips, affected GIFs, direction sheets when applicable, validation, registration/despill reports, continuity, and change report. The builder rejects changed states outside the recorded scope before assembly and again at frame-level comparison.

Every new V2 release needs direction evidence even when look pixels are unchanged. If the neutral cell and all 16 look cells are byte-identical to a previously reviewed release and build inputs/look metadata are unchanged, inherit only that direction evidence with `--inherit-direction-from <reviewed-build>`; the tool records the parent build, parent atlas hash, and cell hashes under the new review. If any direction cell, neutral dependency, look metadata, or build input differs, provide three context-isolated blind reviews using copies of the generated blind-verdict template and independent 16-direction semantics. Direction inheritance never carries visual or semantic verdicts forward.

Every new release also needs exactly three prompt-blind visual-QA verdicts using private copies of the generated template. Visual reviewers receive canonical art, minimal state meanings, normal-size filmstrips, GIFs, and final sheets—not prompts, motion plans, prior verdicts, or answer keys—and must record the contract beat/support fields with distinct reviewer identifiers and pass unanimously. Record all three repeated `--independent-visual-qa` arguments with `petkit review-directions`.

Every new build also needs exactly three anonymous semantic-recognition verdicts. Reviewers classify randomized clips at full and approximate Codex UI size, reject calibration controls, and pass every required confusion pair without seeing row labels, prompts, motion plans, or answer keys. Each verdict must carry a distinct reviewer identifier. Record all three repeated `--semantic-verdict` arguments with `petkit review-directions`.

Read [review-gates.md](references/review-gates.md). Present the evidence and leave the build in review until the user accepts it. Then run `petkit accept` with explicit visual confirmation and a concrete note.

Review publication is all-or-nothing. Acceptance revalidates the fresh semantic
and visual evidence, reviewer records, atlas/canonical-identity bindings, and
inherited direction lineage rather than trusting only the stored summary.

## Install or recover only on request

Use `petkit install --target-root ~/.codex/pets` only after explicit direction. Install rechecks the build-bound manifest and rejects a destination package path that overlaps the editable project. Report the backup. Use `petkit rollback` only when requested; it applies the same path-overlap guard, and you must report restored and displaced packages. A sidecar-free historical backup requires the owner to explicitly authorize `--allow-legacy-backup` after confirming its origin.

## Completion

Report mode, scope, baseline/new build, affected sources, before/after evidence, V2 direction review, exact unchanged states, reversibility paths, acceptance, installation, and backup status.
