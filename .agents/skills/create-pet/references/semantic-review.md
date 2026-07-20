# Anonymous semantic review

Mechanical QA and labeled visual review do not prove that a state is recognizable. Every V2 build must also run the anonymous semantic gate.

Reviewers receive only:

- the canonical identity;
- randomized clip tokens, never row names, on full-size and UI-size filmstrips/GIFs;
- the candidate state meanings as a list;
- calibration controls covering inert, repetitive, cropped/malformed, and identity/material-drift failures.

They never receive prompts, motion plans, source filenames, prior verdicts, answer keys, or claimed repairs. Each reviewer must map every anonymous clip to exactly one state twice: once at full size and once at approximate Codex display size. For every mapping, record the strongest alternative interpretation and visible evidence. A confident label is not enough if the strongest alternative is effectively the same state.

Reviewers must also compare every required confusion pair at both sizes: idle/waiting, idle/work, idle/review, waiting/work, waiting/review, work/review, review/failed, and failed/idle. A pair fails if the labels could be swapped without noticing. Calibration controls must be rejected as invalid evidence; a reviewer that accepts one is not a valid judge for that build.

All three semantic verdicts must independently classify every clip correctly at both sizes, reject every calibration control, and pass every pair. Each verdict carries a distinct reviewer identifier; reusing the same verdict or identifier does not count as independent evidence. Majority voting is not sufficient. The private answer key is used only by the validator after the verdicts are submitted.

If any state fails, repair its complete row or return to semantic concept design. Do not regenerate blindly and do not accept a prompt-dependent explanation.
