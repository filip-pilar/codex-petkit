# Edit review gates

An edit may be accepted only when all applicable checks pass.

- `validation.json` and `frame-inspection.json` report `ok: true`.
- `pet.json` declares `spriteVersionNumber: 2`, the atlas is 8×11, and the direction-review summary passes.
- The build record's `pet.json` hash matches, with the exact project ID, V2 version, and spritesheet path.
- Three blind reviewers with distinct identifiers reach strict-majority direction classifications, and a fourth reviewer with a non-overlapping identifier passes all 16 labeled directions.
- Three additional prompt-blind visual reviewers unanimously pass every standard frame, adjacent transition and loop wrap, per-state anatomy/material/proportion/framing/motion/loop/frame-contribution gate, anti-confusion pair, and cross-state consistency gate.
- Look-row edits replace a complete eight-pose row and preserve cardinal semantics, shared registration, and continuity.
- The before/after sheet makes the intended change legible.
- `change-report.json` lists no changed state outside the recorded scope.
- Every unaffected state is frame-identical to the baseline where exact preservation is expected.
- Affected previews loop without clipping, scale pops, baseline jumps, accidental reversal, frozen motion, or visible chroma residue.
- Identity invariants remain intact unless the approved edit explicitly changes one.
- The action still communicates the correct Codex state.
- The build is immutable, reversible, and not installed as a side effect.
- Acceptance replays the complete review evidence and its atlas, canonical-identity, reviewer, and inherited-lineage bindings.

For variants, also verify the new manifest ID, source link, independent build directory, unchanged source project hashes, and an accepted child-local baseline before the scoped edit.
