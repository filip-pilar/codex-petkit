# Pet Workshop

## Working boundaries

Preserve unrelated work, format/asset validation, exact edit scope, immutable releases, filesystem safety, and reversible installation. Local tests use temporary synthetic fixtures. Run the full suite for changes to build, review, installation, or project transactions; use focused checks for documentation-only changes.

Do not inspect ignored `pets/` projects unless requested. Never commit personal artwork, references, installation backups, or private review data. Installation writes to the user’s Codex pets directory; use temporary target directories in tests.

## Where to look

- Pet creation/editing: the corresponding `.agents/skills/` entry point.
- Sprite geometry and timing: `petkit/contracts/v2.json`.
- Build, review, and installation changes: `docs/PROJECT_FORMAT.md` and `docs/TESTING.md`.
- Artistic quality or generation failures: `docs/ARTWORK.md`.
- Vendored helpers: preserve provenance and checksums in `third_party/openai-hatch-pet/`; update them if changing `petkit/v2scripts/`.

Use image generation for new production artwork. Deterministic image operations are appropriate for extraction, assembly, supplied-frame edits, and cleanup. A successful validator proves format integrity, not visual quality.
