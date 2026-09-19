---
name: edit-pet
description: Edit, restore, or make a separate variant of an existing V2 Codex Desktop pet.
---

# Edit a pet

Make the requested change while preserving identity, unaffected states, and the ability to undo.

Start with `petkit status`. Use [workflow commands](../../../docs/WORKFLOWS.md) for importing, scoped edits, variants, and recovery. Use [artwork guidance](../../../docs/ARTWORK.md) only when new pixels or visual repairs are needed.

Choose the smallest suitable operation: restore or replace supplied frames, re-extract correct artwork, generate an affected animation row, or create an isolated variant for a new treatment. Record allowed states with `plan-edit` before modifying a baseline. Include dependent look rows when changing the neutral idle frame. Look generation currently ingests complete source strips; preserve coherent scale and direction across them.

Inspect changed animation at its real timing and small display size. Compare before/after and verify no state changed outside scope. Check neighboring/dependent states when necessary; unchanged pixels do not require fresh reviewer panels. Repair actual defects and finish the validated release, visual review, and acceptance. Install or restore an installed package when the user's request authorizes it, retaining backups. Report the changed states, preserved states, previews, and undo path.
