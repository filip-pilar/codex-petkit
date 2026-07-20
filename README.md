# Pet Workshop

Pet Workshop is an experimental, community-maintained toolkit for creating and
editing animated V2 pets for Codex Desktop on macOS.

- `$create-pet` guides identity design, nine standard animations, and 16
  coherent look directions.
- `$edit-pet` makes focused, reversible edits and linked variants while proving
  that unrelated states stayed unchanged.
- `petkit` provides deterministic extraction, atlas assembly, validation, QA
  evidence, immutable builds, installation backups, and rollback.

This is not an official OpenAI product and is not endorsed by OpenAI. The pet
format and Codex Desktop integration may change.

## Requirements

- Python 3.11 or newer
- Codex Desktop on macOS for installing and viewing a finished pet
- The Codex `$imagegen` skill when new production artwork must be generated

The deterministic toolkit can be developed and tested without Codex Desktop or
image generation.

## Install for development

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
python -m unittest discover -s tests -v
```

Open a Codex task in the repository root to use its project-local skills:

```text
$create-pet Make me a tiny plush moon moth with warm eyes and a little satchel.
$edit-pet Make my pet's wave more enthusiastic while preserving every other state.
```

Production bitmap generation is delegated to `$imagegen`; `petkit` does not
synthesize replacement artwork.

## Local data

Editable projects live under `pets/<pet-id>/`. The whole `pets/` workspace is
ignored by Git because it can contain personal references, generated art,
private review answer keys, builds, histories, and installed-package backups.
See [ASSETS.md](ASSETS.md) before publishing any artwork.

Projects progress through `brief → identity-approved → generating → review →
accepted`. Resume authority comes from:

```bash
python -m petkit status --project pets/<pet-id>
```

## V2 contract

`petkit/contracts/v2.json` is the supported production contract:

- lossless 1536×2288 WebP;
- 8 columns × 11 rows of 192×208 cells;
- rows 0–8 contain nine standard animation states;
- rows 9–10 contain 16 clockwise look directions in 22.5° steps;
- row 0, column 6 contains the neutral/default look frame;
- `pet.json` declares `spriteVersionNumber: 2`.

Useful commands include:

```bash
python -m petkit --help
python -m petkit contract --version 2
python -m petkit status --project pets/<pet-id>
python -m petkit validate --atlas pets/<pet-id>/builds/<build-id>/spritesheet.webp
```

After a project passes its review and acceptance gates, installation is an
explicit operation:

```bash
python -m petkit install \
  --project pets/<pet-id> \
  --target-root ~/.codex/pets
```

Restart or reopen Codex if its pet list does not refresh. Installation backs up
any displaced package; rollback is also explicit.

## Quality model

Identity approval precedes expensive row production. Standard animations must
pass capability, key-pose, frame, transition, loop, and anonymous semantic
recognition gates. Direction production proceeds mechanics → cardinals → row 9
→ row 10. Builds require isolated blind direction reviews, three unanimous
visual reviews, and three unanimous anonymous semantic reviews. Deterministic
validation never substitutes for visual judgment.

See [docs/PROJECT_FORMAT.md](docs/PROJECT_FORMAT.md),
[docs/TESTING.md](docs/TESTING.md), and [CONTRIBUTING.md](CONTRIBUTING.md).

## License and provenance

Source code, documentation, skills, and synthetic fixtures are licensed under
the [Apache License 2.0](LICENSE). User-supplied and locally generated artwork
is not automatically covered; see [ASSETS.md](ASSETS.md).

Several V2 helper scripts were derived from OpenAI's Apache-licensed
`openai/skills` repository. Their provenance and checksums are retained under
[`third_party/openai-hatch-pet`](third_party/openai-hatch-pet/).
