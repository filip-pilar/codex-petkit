# Implementation status

## Implemented

- V2-only 1536×2288 contract and project schema;
- project-local create and edit skills;
- deterministic row extraction, registration, atlas assembly, and validation;
- one completed-atlas edge-local despill pass;
- coherent cardinal → row 9 → row 10 direction production;
- immutable builds, scoped edits, linked variants, installation backups, and rollback;
- capability, key-pose, frame, transition, loop, direction, visual, and anonymous
  semantic review gates;
- three isolated direction votes, three unanimous visual verdicts, and three
  unanimous semantic verdicts;
- calibration controls for inert, repetitive, cropped, and identity-drift
  failures;
- synthetic end-to-end and safety regressions.

## Distribution boundary

Local production projects are deliberately excluded from source control. They
may contain personal reference images, generated artwork, private answer keys,
machine-specific history, and installation backups. Public CI creates synthetic
projects in temporary directories instead.

## Host-level limitation

The toolkit can validate and install a package but cannot force a running Codex
Desktop process to discard cached Settings state. Refreshing the pet list and
observing live state changes remain manual integration checks.
