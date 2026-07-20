# V2 look-direction production

The final atlas holds 16 clockwise directions in two coherent rows:

- row 9: 000, 022.5, 045, 067.5, 090, 112.5, 135, 157.5;
- row 10: 180, 202.5, 225, 247.5, 270, 292.5, 315, 337.5.

Semantics are viewer-relative: 000 is up/backward gaze, 090 is viewer screen-right, 180 is down/forward gaze, and 270 is viewer screen-left. Neutral is not one of the 16 directions.

Define eye, head, and body mechanics before generation. Use the smallest anatomically sufficient turn at each angle. Eye direction leads, head supports it, and body rotation appears only when needed for clarity. Preserve the same planted lower-body anchor, scale, silhouette family, lighting, asymmetry, and props.

Generate cardinals first, row 9 second, and row 10 last. Ground row 10 in both cardinals and accepted row 9. A look row is one coherent visual object: never repair it cell-by-cell. For any failure in orientation, identity, anatomy, interpolation, or edges, regenerate the complete containing row.

Require one flat chroma background, eight separated complete poses, no labels/text, no guides, no scenery, no cast shadows, no detached effects, no overlap, and no cropping. Keep enough head/facial detail for direction to remain legible at 192×208.
