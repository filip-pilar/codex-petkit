# Editable V2 project format

Each pet is retained under `pets/<pet-id>/`:

```text
pet-project.json                 identity, generation, V2 look gates, status
.petkit-writer.lock              project-local mutating-command serialization
.petkit-recovery/                operation markers for idempotent cancellation recovery
identity.md                      stable art-direction invariants
references/                      original, candidate, and approved identity art
source/frames/<standard-state>/  exact nine-row standard animation frames
source/rows/                      versioned standard and coherent look-row sources
source/cardinals/                approved 000/090/180/270 anchor strip
source/look-mechanics.json       ordered eye/head/body turn specification
builds/build-NNNN/               immutable V2 release or mechanical candidate
reviews/build-NNNN/              anonymous semantic verdicts, independent semantics, blind votes, final QA
history/                         events, acceptance, edit scopes, source backups
backups/installed/               packages displaced by install or rollback
qa/layout-guides/                generation layout references
qa/standard-motion-plan.md       contract-ordered semantic motion plan
qa/capability-audit.json         approved character-capability gate
qa/key-pose-concepts.png         unlabeled pre-strip concept sheet
qa/key-pose-review.json          independent full/UI key-pose gate
```

Projects progress `brief → identity-approved → generating → review → accepted`. `petkit status` is the resume authority: it runs the shared phase-aware preflight and reports the concrete blockers for build, review, acceptance, and installation.

A V1 metadata upgrade preserves its source/build history and current comparison
build, records the former accepted build under `generation.pre_v2_accepted_build`,
and clears the accepted pointer so the first V2 release is reviewed as a fresh
local baseline.

For a V2 accepted build created before integrity binding existed,
`upgrade-project` similarly records `generation.pre_integrity_accepted_build`,
clears the accepted pointer and stale look approvals, and requires a newly
reviewed local baseline. It does not rewrite an immutable legacy build.

## V2 builds

Each release build contains the installable `pet.json` and `spritesheet.webp`, PNG inspection atlas, strict and local validation, source and registered-frame inspection, contact sheet, nine normal-cell-size standard filmstrips, 11 GIFs, registration/despill reports, labeled and blind direction sheets, anonymous full/UI semantic sheets and previews, inert/repetitive/cropped/identity-drift calibration controls, hidden direction and semantic answer keys, continuity measurements, change report, source/build-input hashes, the `pet.json` hash, hashes for the private review authority, and hash-verified artifact-reuse metadata. `petkit build --draft` creates an immutable mechanical candidate with the same deterministic validation and inspectable previews/filmstrips, but omits human-review artifact generation; candidate builds cannot be reviewed, accepted, or installed and never move the project pointers.

PetKit serializes complete mutating commands with one project-local writer lock. Lock state is validated before reentrant use and invalidated before unlock/close, so a cancelled release cannot poison later writers. Metadata replacement and build, identity, and variant transactions use deterministic operation paths recorded under `.petkit-recovery/`; the next mutation idempotently reconciles any marker left by an interrupted recovery. A build copies its source rows and any reusable despill cache into an operation-private snapshot, verifies the copied bytes against the recorded hashes, and consumes only that snapshot. It also rechecks metadata and canonical inputs before publication. On cancellation or another abnormal exit, PetKit re-reads durable authority under that lock: a verified release referenced by the project pointer is preserved, and a verified candidate with its durable `history/candidate-build-NNNN.json` publication record is preserved without moving project pointers. Unreferenced operation-owned builds, staging directories, atomic-write temporaries, and private snapshots are removed or left only at their pre-registered recovery paths with an explicit aggregate error. Historical build IDs are not reused. Manual source edits that bypass PetKit while a command is running remain unsupported; a detected drift aborts publication instead of overwriting newer work.

The project records current and last-accepted builds separately. An edit build cannot erase the accepted pointer. After the first accepted build, a new build requires an active edit scope covering every changed state or canonical dependency; `build` rejects an out-of-scope change before assembly and again after frame comparison. Direction, visual, and semantic reviews are stored outside the immutable build and are bound to the atlas and canonical identity. `review-directions` validates and stages the complete evidence set, then publishes one final review directory atomically. Acceptance replays the required fresh semantic and visual verdicts, reviewer evidence, atlas/identity bindings, and any inherited direction lineage instead of trusting only the summary. Direction evidence may be inherited only when the neutral/look dependency fingerprint matches a previously reviewed release; inherited evidence is retained under the new review with explicit lineage.

## Look-row ownership

Directions are coherent rows, not independently editable cells. Cardinal anchors establish semantics. Row 9 must be approved before row 10 is generated. The assembler normalizes both rows against neutral using one shared registration scale and lower-body/baseline anchor, then applies one final edge-local despill pass.

## Variants

A named alternate treatment is a separate physical project with a distinct ID and `parent_id`. References and sources are copied, never shared mutably; builds, reviews, backups, and installation remain isolated. Create a variant only from its parent's current accepted release with no active edit. Creation verifies the accepted build, review, acceptance, source, authority, and build parameters, then copies from a private verified snapshot and rechecks the child bytes. A cancelled creation removes its registered private snapshot and staging tree. An already-renamed child is preserved only when its exact final metadata hash proves full publication; recovery removes its publication marker and reports the cancellation as post-commit. Otherwise the operation-owned child is removed or retained only at its registered recovery path. Successful creation records the accepted parent binding plus a child-local source, authority, chroma-key, and chroma-threshold snapshot; the first child build must match it. Before a scoped edit, build, review, and accept that unchanged child-local baseline; the parent build is lineage, not the child's edit baseline.

An older unaccepted variant that predates fork snapshots is not discarded.
Running `upgrade-project` explicitly records its current source and authority as a
legacy owner-approved rebaseline; subsequent changes still invalidate that
snapshot before the first build. `upgrade-project` never replaces a valid
schema-2 fork snapshot merely because current source, authority, or chroma
parameters no longer match it.

## Imported packages

`import-package` is a recovery bootstrap for V2 pixels when original project
evidence is unavailable. Its recovered pixels may be repaired before the first
local baseline because no unchanged source-fork claim is made. It does not
manufacture approvals or an accepted edit baseline. Complete the recovered
project's normal local gates, then build, review, and accept its own baseline
before planning a scoped edit.

Frame and row replacement preserve the existing retained-source extension
(`.png` or `.webp`) and write same-extension backups under `history/`. A state
with duplicate PNG/WebP names for one frame is ambiguous and must be repaired
before reversible editing.

Install and rollback resolve the editable project and destination package paths
before creating directories and reject equality or containment in either
direction. Installation revalidates the hash-bound `pet.json`, including its
exact project ID, V2 version, and spritesheet path.
New installation backups include provenance and hashes for both package files;
rollback verifies the staged copy immediately before replacement. Sidecar-free
historical backups require the explicit `rollback --allow-legacy-backup` option
and still must contain a structurally valid manifest that references the copied
`spritesheet.webp`. Package backup and restore copy only those two regular files
and never follow descendant symlinks.
