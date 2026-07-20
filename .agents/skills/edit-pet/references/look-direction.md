# V2 look-direction editing

Directions are clockwise and viewer-relative: 000 up, 090 screen-right, 180 down, 270 screen-left. Neutral is stored separately and is not 000.

The safe production sequence is mechanics → four approved cardinals → coherent row 9 → coherent row 10. Eye direction leads, head turn supports it, and body rotation is only as large as needed while the lower-body anchor, scale, lighting, asymmetry, and props remain stable.

A complete eight-pose row is the minimum generative repair unit. Never patch one direction cell. Regenerate the containing row when orientation, identity, anatomy, interpolation, or edges fail. A changed cardinal system invalidates both rows; a changed row 9 requires row 10 to be regenerated or explicitly revalidated against it.
