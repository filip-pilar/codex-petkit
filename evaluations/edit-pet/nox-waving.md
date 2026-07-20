# Nox waving edit evaluation

- Baseline: accepted Nox `build-0002`
- Request: make only the waving animation more enthusiastic
- Mode: generative, one row
- Result: accepted and installed `build-0003`

## Behavior observed

The skill fixed its scope to `waving`, used the canonical identity, current waving strip, and layout guide as role-specific references, and generated only one four-frame row. It rejected its first usable result because an open-mouth expression drifted from Nox's approved closed smile. A second narrow repair still retained the unwanted expression, so the final repair was again constrained to the mouth while preserving the stronger raised-hand motion.

The final comparison reports waving frames 0–3 changed and all eight other states unchanged. Validation passed with no errors, warnings, edge contact, or transparent RGB residue. The task stopped in review and did not accept or install its own result.

## Independent review and recovery proof

The parent task inspected the final strip, waving preview, contact sheet, before/after sheet, live validation, and exact frame comparison before accepting build-0003. Installing it backed up installed build-0002. A real rollback restored build-0002's exact spritesheet hash, retained displaced build-0003, and a final install restored accepted build-0003.

The production artwork and review bundle remain local; public workflow tests exercise equivalent changed-state, backup, rollback, and restoration assertions with synthetic fixtures.
