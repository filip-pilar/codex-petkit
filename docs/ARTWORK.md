# Artwork guidance

Astra directs the process; the image-generation tool creates new artwork. Improvements in reasoning do not guarantee consistent generated sprite geometry. Choose a generation strategy based on the actual outputs, without requiring model comparisons before normal work.

## Identity and extraction

Use a retained canonical reference to preserve silhouette, proportions, palette, material, markings, asymmetry and props. A small full-body sprite needs padding and a readable silhouette. For `ingest-row`, provide one horizontal strip with separated poses and the frame count in `petkit contract`. Use the project's flat chroma background; avoid baked grids, labels, scenery, shadows and cropping. Try re-extraction before regenerating visually correct source art. Layout guides are available when useful.

## Motion

Inspect GIFs at contract timing and approximate desktop size, including the last-to-first transition. Look for malformed anatomy, identity drift, scale jumps, clipping, fringe and confusing action. Intentional holds are allowed; duplicate-frame warnings ask for judgment. Entirely static rows and malformed assets remain validation failures.

`idle` is a calm loop with a usable reduced-motion first frame. `running-right` and `running-left` are locomotion; mirror only when asymmetry and lighting allow it. `waving` greets, `jumping` lifts and lands, `failed` reacts to an error, `waiting` expects input, `running` conveys active work (not locomotion), and `review` conveys inspection. Adapt these meanings to the character; no fixed limb choreography or mandatory props are required.

## Look directions

Rows 9 and 10 contain 16 clockwise look poses. 000 is up, 090 viewer-right, 180 down, 270 viewer-left; neutral is separate at row 0 column 6. Follow the exact order in the contract. Keep scale, baseline, lighting and identity consistent across both strips. Cardinal studies or a mechanics plan can help ambiguous turn systems, but are not build prerequisites. Inspect all directions for a new pet and affected/dependent directions for edits.

The assembler registers source strips against neutral. Whole-row regeneration is often the most coherent repair; use judgment rather than treating repeated regeneration as a quality guarantee. Additional independent review is useful when a specific uncertainty remains, not as a compulsory vote count.
