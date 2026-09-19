# Pet Workshop

Create, edit, preview and safely install animated Codex Desktop pets.

Use the creation and editing skills to generate artwork and inspect animations. The `petkit` CLI handles sprite geometry, packaging, scoped comparisons, versioned builds and undo.

```text
$create-pet Make a tiny plush moon moth with warm eyes and a satchel.
$edit-pet Make its wave more enthusiastic. Preserve every other animation.
```

## Get started

Requires Python 3.11+, Codex with image generation, and Codex Desktop on macOS for installation. Open a Codex task in this repository to discover the two project-local skills.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m petkit --help
```

The ordinary workflow is **create or edit → build → inspect animation → record review → accept → install when authorized**. A focused edit preserves unrelated states exactly. Optional deep-review diagnostics provide direction and animation checks.

## What the toolkit protects

- V2 geometry: lossless 1536×2288 WebP, 8×11 cells of 192×208, nine animation states and sixteen clockwise look directions, plus the neutral frame.
- Correct manifests, transparency, visible assets, frame counts and non-static animation.
- Immutable releases, source hashes, scoped changes and recovery from interrupted operations.
- Staged installation with verified backups and rollback. Installation never happens as a side effect of generation or build.

Mechanical validation cannot establish visual quality. Inspect the previews and record observations tied to the exact release. Intentional repeated frames produce warnings for review rather than automatic rejection.

## Commands and local data

```bash
python -m petkit status --project pets/my-pet
python -m petkit build --project pets/my-pet
python -m petkit review --project pets/my-pet --build-id build-0001 \
  --confirm-visual-qa --review-note "Observed identity, motion, directions and loop quality."
python -m petkit accept --project pets/my-pet --confirm-visual-qa --review-note "Ready."
python -m petkit install --project pets/my-pet --target-root ~/.codex/pets
python -m petkit rollback --project pets/my-pet --target-root ~/.codex/pets
```

Use truthful observations in review notes after inspecting the actual artifacts. Editable projects, generated art and backups stay in ignored `pets/`. Never publish that directory by default. See [workflow commands](docs/WORKFLOWS.md), [artwork guidance](docs/ARTWORK.md), [project format](docs/PROJECT_FORMAT.md), and [testing](docs/TESTING.md).

Experimental, community-maintained software; not an official OpenAI product. The supported V2 contract is recorded in `petkit/contracts/v2.json`; Codex integration can change. Restart/reopen Codex if installed pets do not refresh.

## License and provenance

Code, documentation, skills and synthetic fixtures: Apache-2.0. Personal/generated artwork is separate; see [ASSETS.md](ASSETS.md). Upstream-derived V2 helpers retain their provenance and checksums in [third_party/openai-hatch-pet](third_party/openai-hatch-pet/). See [CONTRIBUTING.md](CONTRIBUTING.md).
