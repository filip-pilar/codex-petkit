# Project operations

Run `python3 -m petkit <command> --help` for arguments. Commands return JSON. `status` is read-only and reports phase blockers; its optional look-study metadata is not a build gate.

## Create

1. `init --root pets --id <id> --name <name> --description <text> --concept <text>` creates an ignored editable project.
2. `approve-identity --project pets/<id> --image <path>` retains and binds the selected reference. Choose it under the user's existing creative authorization.
3. `contract --version 2` supplies counts and timing. `make-guides` is optional. Generate source strips, then `ingest-row --project pets/<id> --state <state> --strip <path>` for all 11 rows. Look rows can be ingested in either order; no cardinal approval or mechanics JSON is required.
4. `build --project pets/<id>` creates an immutable validated release with a contact sheet, filmstrips, animated previews and change report. Inspect those artifacts and repair defects.
5. `review --project pets/<id> --build-id <build-id> --confirm-visual-qa --review-note "<observations>"` binds visual inspection to that exact build. A note should cover identity, motion/loops, state readability, directions and relevant warnings; it must reflect actual inspection.
6. `accept --project pets/<id> --build-id <build-id> --confirm-visual-qa --review-note "<result>"` selects the release. This is a local readiness record, not an extra user-approval ceremony.
7. When authorized, `install --project pets/<id> --target-root ~/.codex/pets` installs the accepted package and backs up what it replaces. Reopen Codex if its pet list has not refreshed. Verify representative states and look tracking in the actual app when available; otherwise report that integration check as outstanding.

## Edit and variants

Use `plan-edit --project pets/<id> --mode generative --outcome "<change>" --allow-state waving` before modifying a baseline. Deterministic edits use `--mode deterministic`. Repeat `--allow-state` for dependencies. Sources and final pixels are checked against scope. Build, inspect the affected preview and before/after, review, and accept. Unchanged animations need no repeated panel review.

`variant` makes an independent copy from a verified accepted parent. The child can be changed immediately for a new overall treatment. For a narrowly scoped child edit, make a local build and use `plan-edit`; an unchanged child does not need a separate review and acceptance cycle first. Parent sources are never shared mutably. `import-package` recovers V2 pixels when editable sources are unavailable; inspect the recovered art and create a local build before a scoped edit.

`restore-frame` / `restore-row` restore retained source backups. `rollback --project pets/<id> --target-root ~/.codex/pets` restores the latest installed-package backup and preserves the displaced package. Sidecar-free historical backups require the explicit `--allow-legacy-backup` option after checking their origin.

## Optional diagnostics and older projects

`build --draft` creates an un-installable inspection candidate without changing current/accepted pointers. `build --deep-review` opts into the historical independent direction, visual and semantic review artifacts and `review-directions` requirements. Use it only when that extra protocol is wanted. Historical review records remain verifiable; a new ordinary build uses concise visual review. `set-look-mechanics`, `approve-cardinals` and `approve-look-row-9` remain available as optional studies for existing workflows.

If an old project predates integrity binding, `upgrade-project` preserves history and establishes a fresh baseline. Never rewrite old immutable builds or manually delete transaction recovery markers. A cancelled operation is reconciled by the next mutation; report any remaining recovery errors.
