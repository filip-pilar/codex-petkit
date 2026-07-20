# Animation-quality regression case study

An early local production build passed mechanical atlas validation and the old
direction checks but failed direct visual inspection. The retained local
artifact is not distributed with the public repository.

Observed problems included nearly static idle motion, implausible locomotion
support phases, a weak return frame in the wave, an unreadable jump, proportion
drift, malformed limbs, and several states that were understandable only when
their labels or prompts were visible.

The failure led to these public safeguards:

| Failure | Preventive gate | Final evidence |
| --- | --- | --- |
| Indistinguishable states | Capability audit, semantic signatures, anti-confusion matrix, and prompt-blind key-pose review | Randomized full/UI-size clips, pairwise confusion checks, calibration controls, and three unanimous semantic reviewers |
| Bad anatomy or support | Rejection-first inspection of every frame and transition | Three prompt-blind visual reviewers record anatomy, support, contribution, transitions, and loop wrap |
| Prompt-biased review | Reviewers do not receive generation prompts or motion plans | Review inputs are declared and validated |
| Over-trust in mechanical checks | Geometry checks are treated as necessary but insufficient | Human visual and semantic gates remain mandatory |

Reviewer identifiers are process evidence rather than cryptographic proof.
Reviewers must still be context-isolated and must not see previous verdicts or
private answer keys.
