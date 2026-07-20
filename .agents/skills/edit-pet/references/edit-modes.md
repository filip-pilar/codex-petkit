# Edit modes

Choose the mode before touching sources.

## Deterministic

The desired pixels already exist and only placement, extraction, ordering, transparency, packaging, metadata, derivation, or restoration changes. Do not invoke image generation. Prefer a frame operation over a row operation and a row operation over a whole-atlas rebuild.

Examples: retry extraction with stable slots, replace one supplied frame, restore a backup, reorder frames, safely mirror direction, rebuild previews, clean transparent RGB, or correct the manifest.

## Generative

The request needs genuinely new visual content: a changed expression or gesture, repaired anatomy or identity, redesigned motion, added prop, or new art treatment. Generate only the smallest affected state set and ground every call in the approved identity.

## Linked variant

The change is a named alternate treatment—season, costume, palette, material, style, or recurring prop—that should coexist with the source pet. Clone first with `petkit variant`, verify its distinct ID, and edit only the clone.

If the request mixes modes, perform deterministic corrections first. This keeps image generation focused on the visual work it alone can solve.
