# Character capability audit

Before standard-row generation, inventory what the approved character can visibly express:

- head/body separation and useful center-of-mass range;
- arms, legs, appendages, joints, and whether they can make readable gestures;
- eyes or other face cues at thumbnail size;
- stable interaction surfaces or recurring props;
- asymmetry that can survive mirroring and small display size;
- silhouette changes that do not alter identity or apparent scale.

Map each requested state to at least one strong capability. If a state has only a weak eye-only cue, mark it infeasible and redesign it. A stable simple prop may be authorized when it provides the missing action cue, but it must be designed before prompting, remain legible, and not introduce labels, text, detached effects, or malformed anatomy.

The audit is a gate, not a description exercise. A character that cannot distinguish waiting, active work, and review with its available anatomy must not proceed to full-strip generation until the semantic design changes.

Persist the gate as `qa/capability-audit.json` with `schema_version: 1`, `pass: true`, and one contract-ordered entry per standard state. Each entry must include `approved: true`, a non-empty `capability`, `thumbnail_cue`, and `anti_confusion`; `eyes-only` is not an approved capability.
