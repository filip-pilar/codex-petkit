# Testing and evidence

Run `python3 -m unittest discover -s tests -v`.

The suite covers the V2-only contract; 8×11 geometry and neutral cell; unused-cell transparency; exact extraction/recomposition; partial resume; semantic design/capability/key-pose gates; exact duplicate-beat and standard-edge rejection; cardinal/row sequencing; full registered assembly; strict V2 validation; one final despill; 11 previews; direction QA artifacts; three-vote blind majority; anonymous full/UI-size semantic recognition and calibration rejection; independent semantics/final QA; acceptance gating; frame-granular edit scope; install backup/rollback; malicious package paths; and linked-variant isolation.

Synthetic fixture art tests deterministic behavior only. Production art must come from `$imagegen` or user sources.

## Real visual QA

A production build additionally requires:

1. labeled inspection of all 16 directions, including facial/head zooms;
2. three context-isolated blind A/B reviewers who do not receive labels, prompts, or the answer key;
3. strict-majority combination with cardinal pairs as hard gates;
4. three prompt-blind independent visual reviewers who inspect canonical identity, normal-size filmstrips, and animated previews without seeing generation prompts or the motion plan;
5. unanimous frame-by-frame anatomy, frame-contribution, transition/loop, identity/material, scale/proportion, framing, motion, and cross-state consistency verdicts;
6. three anonymous semantic reviewers who classify randomized clips at full and approximate UI size, reject every inert/repetitive/cropped/identity-drift calibration control, and pass every required confusion pair without labels, prompts, or the answer key;
7. human review of any deterministic continuity warnings.

The final integration observation remains in Codex Settings → Pets: select the installed pet and verify look tracking plus representative standard states.
