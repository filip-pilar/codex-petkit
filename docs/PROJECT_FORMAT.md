# Editable V2 project format

Projects live under ignored `pets/<id>/`. `pet-project.json` tracks identity, retained sources, current and accepted builds, and active edit scope. `references/` holds supplied and chosen identity art; `source/rows/` and `source/frames/` retain editable animation sources. Optional legacy `look` studies are preserved but do not control new builds.

`builds/build-NNNN/` contains an immutable `pet.json`, lossless `spritesheet.webp`, validation reports, contact sheet, filmstrips, eleven animated previews, registration/despill reports, source/package hashes, and a change report. New builds record `workflow_version: 2` and `review_profile: visual`. Optional deep-review releases record `review_profile: independent` and retain the older diagnostic artifacts. Candidate builds cannot be accepted or installed.

`reviews/build-NNNN/review-summary.json` records observed visual quality. Schema 4 binds the review to the atlas, manifest, canonical identity and inspection artifacts. Acceptance and installation replay these bindings; neither a passing format check nor a fabricated note proves visual quality. Historical schema-3 independent review packages retain their original validation path.

`history/` retains edit scopes and acceptance records. After an accepted baseline, builds require a recorded edit scope; source and final frame comparisons reject unrelated changes. A variant copies verified parent sources and records its fork snapshot as provenance. It can develop independently before its first release. A source import is a recovery bootstrap, not proof that recovered pixels are visually correct.

## Filesystem integrity and reversibility

Project mutations are serialized. Builds consume verified private input snapshots and recheck live authority before publication. Durable recovery markers support cancellation and crash recovery. Immutable history and build IDs are preserved. Manual edits during an active mutation remain unsupported.

Install/rollback reject overlapping project and package paths and symbolic descendants. The manifest is bound to its build, including pet ID, sprite version and spritesheet path. Replacement is staged; displaced packages receive hash-bound backups. Rollback verifies the staged backup before replacement and retains the displaced version. Only the two regular package files are installed. Legacy backups without integrity sidecars require explicit opt-in.
