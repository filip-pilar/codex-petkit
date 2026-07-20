# Nox deterministic jump-repair evaluation

- Project: linked isolated evaluation variant
- Parent: `nox`
- Baseline: `build-0001`, intentionally extracted with component centering
- Result: accepted `build-0002`, not installed

## Case

The baseline atlas was mechanically valid, but its jumping frames had baselines spanning only five pixels, visually flattening the action. The request explicitly required a deterministic repair from retained pixels and prohibited image generation.

The skill recorded an edit scope allowing only `jumping`, then re-ingested the retained row with stable-slot extraction. The new baselines were `203 → 163 → 141 → 166 → 203`. The build report showed all five jumping frames changed, all eight other states frame-identical, no unexpected state, and `scope_ok: true`.

The linked project has its own ID, build history, copied source files, and `parent_id: nox`; changes did not alter the source project. No image-generation call occurred, and the task stopped in review before independent acceptance.

The production artwork and review bundle remain local; public tests exercise the same scope and isolation invariants with synthetic fixtures.
