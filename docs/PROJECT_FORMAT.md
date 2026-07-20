# Editable V2 project format

Each pet is retained under `pets/<pet-id>/`:

```text
pet-project.json                 identity, generation, V2 look gates, status
identity.md                      stable art-direction invariants
references/                      original, candidate, and approved identity art
source/frames/<standard-state>/  exact nine-row standard animation frames
source/rows/                      versioned standard and coherent look-row sources
source/cardinals/                approved 000/090/180/270 anchor strip
source/look-mechanics.json       ordered eye/head/body turn specification
builds/build-NNNN/               immutable V2 package and deterministic QA
reviews/build-NNNN/              anonymous semantic verdicts, independent semantics, blind votes, final QA
history/                         events, acceptance, edit scopes, source backups
backups/installed/               packages displaced by install or rollback
qa/layout-guides/                generation layout references
qa/standard-motion-plan.md       contract-ordered semantic motion plan
qa/capability-audit.json         approved character-capability gate
qa/key-pose-concepts.png         unlabeled pre-strip concept sheet
qa/key-pose-review.json          independent full/UI key-pose gate
```

Projects progress `brief → identity-approved → generating → review → accepted`. `petkit status` is the resume authority.

## V2 builds

Each immutable build contains the installable `pet.json` and `spritesheet.webp`, PNG inspection atlas, strict and local validation, source and registered-frame inspection, contact sheet, nine normal-cell-size standard filmstrips, 11 GIFs, registration/despill reports, labeled and blind direction sheets, anonymous full/UI semantic sheets and previews, inert/repetitive/cropped/identity-drift calibration controls, hidden direction and semantic answer keys, continuity measurements, change report, and source hashes.

The project records current and last-accepted builds separately. An edit build cannot erase the accepted pointer. `plan-edit` records allowed states and `build` rejects any out-of-scope pixel change. Direction, visual, and semantic reviews are stored outside the immutable build and are hash-bound to that atlas. Three prompt-blind visual verdicts and three anonymous semantic verdicts must pass unanimously.

## Look-row ownership

Directions are coherent rows, not independently editable cells. Cardinal anchors establish semantics. Row 9 must be approved before row 10 is generated. The assembler normalizes both rows against neutral using one shared registration scale and lower-body/baseline anchor, then applies one final edge-local despill pass.

## Variants

A named alternate treatment is a separate physical project with a distinct ID and `parent_id`. References and sources are copied, never shared mutably; builds, reviews, backups, and installation remain isolated.
