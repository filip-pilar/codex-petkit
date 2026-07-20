# Pet Workshop Plan

## Purpose

Maintain an open-source, project-local workshop for making, editing, validating, and installing high-quality V2 Codex pets. It is not a general sprite editor or a compatibility layer for other pet formats.

The user should describe a character or focused change in ordinary language. The local skills handle identity grounding, animation/direction planning, image generation, deterministic assembly, QA, reversible builds, and explicit installation.

## Confirmed product decisions

- Two focused local skills: `$create-pet` and `$edit-pet`; no router or global installation.
- `petkit` is shared deterministic tooling, not a user-facing skill.
- V2 is the only production/import/build contract: 8×11, 1536×2288, `spriteVersionNumber: 2`.
- Rows 0–8 retain the standard animation contract. Rows 9–10 hold 16 clockwise look directions.
- A canonical full-body identity is generated or selected internally; the user does not need to supply one.
- Identity approval happens before expensive animation production.
- Look production is mechanics → four cardinals → coherent row 9 → coherent row 10.
- 000 is up, not neutral. Neutral is stored separately at row 0 column 6.
- Look cells are never generated, patched, or normalized independently; the minimum repair unit is one complete eight-pose row.
- Registration uses one shared scale and lower-body/baseline anchor against neutral.
- Exactly one edge-local despill pass runs on the completed atlas.
- Deterministic validation and visual QA are complementary; neither substitutes for the other.
- Three isolated blind reviews, strict majority, labeled 16-direction semantics, continuity review, and independent final QA gate acceptance.
- Before standard-row generation, record distinct state intent, silhouette, face, rhythm, beats, amplitude, and anti-overlap rules; final QA must explicitly test the complete semantic confusion set at display size.
- The build is blocked until the project contains a passing motion plan, capability audit, unlabeled key-pose concept sheet, and independent full/UI key-pose review.
- Before full-strip generation, run a character capability audit and anonymous key-pose recognition gate. Every state must have an observable thumbnail-size cue; abstract states may use one controlled state-local prop when the audit proves it is necessary.
- Every V2 build emits randomized anonymous semantic filmstrips/GIFs at full and approximate Codex UI size plus inert, repetitive, cropped/malformed, and identity-drift calibration controls. Three isolated semantic reviewers must independently classify every clip at both sizes, reject every calibration control, and pass the full confusion set without labels, prompts, plans, or answer keys.
- Preserve unaffected source art and alpha geometry, keep builds immutable, and make all source edits reversible. The mandatory completed-atlas despill may change only contaminated edge RGB, and that migration-only cleanup must be reported explicitly.
- Installation and rollback remain explicit and backup any displaced package.
- Older local project metadata may be upgraded once so its nine accepted standard source rows, alpha geometry, and immutable history are preserved; there is no V1 production or import path.

## Everyday creation workflow

1. Create/resume an editable project and concise character brief.
2. Generate/select and approve one canonical identity.
3. Generate, ingest, preview, and narrowly repair the nine standard animations.
4. Record all 16 eye/head/body direction mechanics.
5. Generate and approve coherent 000/090/180/270 cardinals.
6. Generate and approve coherent row 9.
7. Generate row 10 grounded in identity, mechanics, cardinals, and row 9.
8. Deterministically register, assemble, despill once, validate, and render QA.
9. Run blind/labeled/continuity/final visual reviews plus the anonymous full/UI semantic review; regenerate a full failing row.
10. Present the review build; accept and install only with explicit user direction.

## Everyday editing workflow

Classify the request as deterministic, generative, or linked variant; record exact allowed states and invariants; touch the smallest safe scope; rebuild; prove unaffected states unchanged; rerun V2 direction evidence; accept/install only explicitly.

Standard animations permit reversible frame-level deterministic fixes. A generative look repair always replaces all eight poses in its row. Changes to cardinal semantics require both look rows to be regenerated in order.

## Editable source and evidence

Retain original references, approved identity, mechanics, cardinals, selected row sources, exact standard frames, build lineage/hashes, validation, direction QA, acceptance records, edit scopes, and installed-package backups. Temporary rejected generations may be pruned once the latest build is reviewed and installed; rollback history and intentional regression fixtures remain.

## Definition of done

- Both local skills pass structural validation and accurately describe V2 gates.
- The deterministic suite exercises full V2 creation/edit/review/install/rollback behavior, including semantic design-gate, duplicate-beat, edge-contact, reviewer-identity, calibration, and holdout rejection paths.
- Public regression tests reproduce the important mechanical, semantic, and review failures with synthetic fixtures rather than production artwork.
- Installing an accepted synthetic V2 build creates a recoverable backup of the prior package; rollback and reinstall are verified by the deterministic suite.
- Live Codex behavior remains an explicit manual integration check for each production pet.

## Status

The V2-only contract, toolkit, skills, project format, deterministic regressions, anonymous semantic-review artifacts, calibration controls, and UI-size semantic schema are implemented. Public tests use synthetic temporary fixtures. Local production projects and their review evidence live under the ignored `pets/` workspace and are not part of the source distribution.
