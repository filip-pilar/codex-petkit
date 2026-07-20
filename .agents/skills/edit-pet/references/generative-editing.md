# Generative row editing

Use four reference roles when available:

1. approved canonical image: identity authority;
2. current state strip: pose sequence, timing, and edit baseline;
3. original user references: markings, materials, or features not clear in the canonical view;
4. layout guide: frame count and spacing only, never visual style.

State the single intended delta and explicitly preserve silhouette, proportions, face, palette, material, markings, asymmetry, scale, baseline, lighting, and every prop not named by the request.

Require exactly the contract frame count in one horizontal strip, complete separated full-body poses, the project chroma key, and a loopable action. Ground the edit in an observable semantic signature and capability audit; if the edited state is no longer recognizable without its label at UI size, reject it. Forbid scenery, labels, text, numbers, shadows, detached effects, cropping, overlapping poses, motion blur, and colors near the key unless a simple controlled prop is explicitly part of the approved semantic design.

For `look-a` or `look-b`, the complete eight-pose row is the minimum safe unit. Never generate or patch a single direction cell; follow the V2 cardinal → row 9 → row 10 sequence.

Inspect the generated strip itself before ingestion, then inspect the normal-size registered filmstrip and animated preview. Review every frame, adjacent transition, and last→first wrap. A technically extractable strip still fails for one malformed limb, proportion or scale pop, material/lighting drift, purposeless frame, implausible support phase, crop, bad loop, unreadable edit, wrong state, or inert motion. Prompt intent never outweighs visible output.
