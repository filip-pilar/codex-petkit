# Nox V2 upgrade evaluation

## Outcome

Nox was upgraded in place from a retained local project to an accepted V2 build and installed as a Codex pet. The final atlas was 1536×2288 and `pet.json` declared `spriteVersionNumber: 2`.

## Production path

- Preserved the canonical Nox identity and all nine standard source rows.
- Recorded mechanics for all 16 clockwise look directions.
- Generated and approved the four cardinals before row 9.
- Rejected incomplete or semantically weak row-9 generations, then approved one coherent eight-pose row.
- Generated row 10 only after row-9 approval. A first coherent result was visually too small; it was rejected.
- Applied one deterministic uniform 1.061 vertical correction to the entire replacement row, never a pose-specific patch, then rebuilt.
- Kept rejected `build-0004` as immutable comparison evidence and accepted `build-0005`.

## Preservation and validation

The V2 migration's mandatory single completed-atlas despill changed contaminated edge RGB in standard rows. The migration report proved all 57 standard alpha masks remained identical and recorded the cleanup explicitly; the following build proved the standard rows and row 9 were unchanged while only row 10 changed.

Strict V2 validation passed with zero errors and warnings. All 16 labeled directions passed, three isolated blind verdict files passed strict-majority validation against the hidden answer key, and an independent final reviewer approved identity, anatomy, accessories, registration, transparency, neutral behavior, standard animation fitness, and the bound atlas hash. Continuity warnings were retained and overridden only after visual inspection confirmed intentional gaps between antennae and feet, not holes or snapping.

## Installation recovery proof

Installation created a recoverable backup of the previous package. The pipeline restored that backup and verified its prior atlas hash, then reinstalled accepted `build-0005` and verified the final V2 manifest, dimensions, and atlas hash. No backup was deleted.

The remaining host-level check is to refresh Codex Settings and confirm that its cached “Update Nox” affordance disappears, then observe representative standard and look states in the live app.
