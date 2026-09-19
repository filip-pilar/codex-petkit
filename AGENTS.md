# Pet Workshop — GPT-6 Astra

This repository's agent workflows target GPT-6 Astra exclusively. Follow the user's requested outcome and authorization; skill guidance supplies defaults, not extra approval requirements.

## Working boundaries

Preserve unrelated work, format/asset validation, exact edit scope, immutable releases, filesystem safety, and reversible installation. Local tests use synthetic disposable fixtures with no production access; run relevant checks and fix regressions without asking at each step. Complete implementation and validation before handing work back. Run the full suite for changes to build, review, installation, or project transactions; use focused checks for documentation-only changes.

Do not inspect ignored `pets/` projects unless requested. Never commit personal artwork, references, installation backups, or private review data. Installing a pet needs user authorization, which may already be included in their request. Publication and external actions require their own authorization.

## Where to look

- Pet creation/editing: the corresponding `.agents/skills/` entry point.
- Sprite geometry and timing: `petkit/contracts/v2.json`.
- Build, review, and installation changes: `docs/PROJECT_FORMAT.md` and `docs/TESTING.md`.
- Artistic quality or generation failures: `docs/ARTWORK.md`.
- Vendored helpers: preserve provenance and checksums in `third_party/openai-hatch-pet/`; update them if changing `petkit/v2scripts/`.

Use image generation for new production artwork. Deterministic image operations are appropriate for extraction, assembly, supplied-frame edits, and cleanup. A successful validator proves format integrity, not visual quality.
