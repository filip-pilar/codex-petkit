# Validation

The default workflow targets GPT-6 Astra. Synthetic tests prove deterministic behavior, not artistic quality or model performance.

Run `python3 -m unittest discover -s tests -v` for build/review/install/project changes. Focused suites cover geometry, validation, filesystem safety, workflow transactions and the concise Astra review path. Tests use temporary synthetic projects; they never install to real user directories. Preserve coverage of corrupted manifests, stale reviews, source drift, edit-scope violations, cancellation recovery, and reversible installation. Historical independent-review tests exercise the opt-in diagnostics path.

Visual checks inspect actual animation timing, identity, direction and small-size readability. Duplicate holds are warnings; malformed geometry, missing/empty assets, static rows and clipping remain failures. Review affected states and dependencies for edits. No fixed number of reviewers is required by the ordinary workflow.

A real generated pet and Codex Settings → Pets remain the integration check for artwork and runtime behavior. State explicitly when those were not exercised; do not use synthetic fixture passes as artistic evidence.
