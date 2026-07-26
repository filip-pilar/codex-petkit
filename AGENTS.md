# Repository instructions

## Purpose

Pet Workshop is a Python 3.11+ toolkit and a pair of project-local skills for
creating and editing V2 Codex Desktop pets. V2 is the only supported production
contract.

Keep changes focused. Preserve filesystem safety, project history, immutable
builds, review isolation, and reversibility.

## Repository map

- `petkit/`: deterministic CLI, project state, builds, validation, and image processing.
- `petkit/contracts/v2.json`: authoritative V2 geometry and state contract.
- `.agents/skills/create-pet/`: production pet-creation workflow.
- `.agents/skills/edit-pet/`: focused, reversible pet-editing workflow.
- `tests/`: synthetic contract, safety, validation, regression, and workflow tests.
- `docs/`: project format, testing model, implementation status, and case studies.
- `petkit/v2scripts/`: vendored upstream-derived helpers.
- `pets/`: ignored local user data, not repository source.

## Before changing anything

- Inspect `git status` and preserve unrelated work.
- For a production creation or edit, read the applicable local `SKILL.md`
  completely and follow its referenced material.
- For an existing pet project, use
  `python3 -m petkit status --project pets/<id>` as resume authority.
- Do not inspect or modify ignored `pets/` projects unless the task explicitly
  targets that project.

## Safety and data boundaries

- Never commit files from `pets/`, production artwork, private answer keys,
  installation backups, or machine-local histories.
- Tests must use synthetic fixtures in temporary directories.
- Do not modify an immutable build, backup, installed package, or a variant's
  source project in place.
- Do not accept, install, roll back, or write under `~/.codex/pets` without
  explicit user direction.
- Use ImageGen for new production artwork. Pillow and deterministic transforms
  are appropriate for tooling and synthetic test fixtures, not replacement art.
- Treat `look-a` and `look-b` as complete-row repair units; never patch one
  generated direction cell.
- Mechanical validation does not replace visual review.

## Sources of truth

- V2 geometry and state definitions: `petkit/contracts/v2.json`.
- Project metadata validation: `petkit/schemas/project.schema.json`.
- Editable project layout: `docs/PROJECT_FORMAT.md`.
- Test and review expectations: `docs/TESTING.md`.
- Production workflows: the applicable `.agents/skills/*/SKILL.md`.

Update code, tests, schemas, documentation, and skill guidance together when a
change crosses those boundaries.

## Vendored files

Files under `petkit/v2scripts/` and
`petkit/references/v2/animation-rows.md` are checksum-tracked.

When changing one:

1. Explain the divergence in `third_party/openai-hatch-pet/UPSTREAM.md`.
2. Update `third_party/openai-hatch-pet/SHA256SUMS`.
3. Verify the checksums.

Use `shasum -a 256 -c third_party/openai-hatch-pet/SHA256SUMS` on macOS or
`sha256sum -c third_party/openai-hatch-pet/SHA256SUMS` on Linux.

## Validation

Run focused tests while iterating, for example:

```bash
python3 -m unittest tests.test_contract -v
python3 -m unittest tests.test_project_schema -v
python3 -m unittest tests.test_safety -v
python3 -m unittest tests.test_validation -v
```

Before handing off a code change, run:

```bash
python3 -m unittest discover -s tests -v
```

For packaging or package-data changes, also build a wheel and verify that the
contract, schema, references, and V2 helper scripts are present.

Report checks actually run and any manual Codex Desktop verification that
remains.
