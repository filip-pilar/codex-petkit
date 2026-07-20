# Contributing

Contributions are welcome. Keep changes focused, preserve existing safety and
reversibility guarantees, and do not commit local pet projects or generated
production artwork.

## Development setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
python -m unittest discover -s tests -v
```

Tests must use synthetic fixtures created in temporary directories. A test may
not depend on a contributor's `pets/` directory, installed Codex package, or
private QA answer key.

When changing a vendored file under `petkit/v2scripts/`, describe the change in
`third_party/openai-hatch-pet/UPSTREAM.md` and update
`third_party/openai-hatch-pet/SHA256SUMS`.

By submitting a contribution, you agree that it is licensed under the Apache
License 2.0 and that you have the right to provide it.
