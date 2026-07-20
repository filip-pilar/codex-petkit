# Locky V2 rejected regression evaluation

- Input: approved canonical art at `references/approved/canonical-base.png`
- Historical result: technically accepted repository-local `build-0002`, subsequently rejected by the user on direct visual inspection
- Installation: deliberately not performed

## Observed failure

The review gate over-weighted intended state descriptions and under-weighted visible animation quality. The independent reviewer was allowed to inspect the motion plan and returned positive descriptions that the final frames do not support.

Direct review found near-static idle motion; questionable airborne/support phases in both runs; an underdeveloped four-frame wave with a near-duplicate return; a jump that reads as translated/cropped posing rather than takeoff and landing; unstable head/body proportions in failure; malformed waiting limbs; and a work row that resembles dabbing and drifts from Locky's blue material. These are authoritative failures even though the technical and old review schemas passed.

## Iteration evidence

- Jump extraction initially made the character too small. The new `motion-components` path measures separated poses at one shared model scale while preserving a bounded vertical arc; final model heights are 176–178 pixels with no edge contact.
- Row 9 passed as one coherent eight-direction generation with a shared registered scale and baseline.
- The first row 10 was rejected for a yaw reversal and retained at `qa/generated/look/row-10-v1-rejected-yaw-flip.png`.
- The coherent replacement showed a uniform cross-row scale mismatch. One documented complete-row correction of 1.06× horizontally and 1.03× vertically improved the 337.5→000 boundary area ratio from 1.208 to 1.115. No direction cell was edited independently.

Strict V2 validation and direction review passed, establishing package mechanics only. They did not establish animation quality. The old verdict shape is represented by a synthetic public regression: the strengthened validator rejects it because it lacks prompt-blind inputs, per-frame anatomy/contribution observations, transition and loop evidence, per-state quality gates, cross-state consistency gates, and three unanimous reviewers. The production artwork and review bundle remain local.
