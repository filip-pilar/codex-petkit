# V2 standard animation direction

Use `python3 -m petkit contract --version 2` as the authority for geometry, frame counts, timing, and look-row order. This file covers the nine standard animation rows.

## Shared row contract

- One horizontal sequence containing exactly the requested number of separated full-body poses.
- Same character identity, silhouette, proportions, face, markings, palette, material, lighting, and prop design as the approved canonical image.
- Compact readable sprite silhouette with stable scale and baseline.
- Flat uniform project chroma background. No shadows, scenery, labels, text, grids, detached effects, neighboring overlap, or cropped parts. A simple state-local prop is allowed only when the capability audit and semantic design authorize it as the decisive action cue; it must be stable, legible, and physically attached or consistently placed.
- Each frame has a named physical purpose and advances a plausible action. Preserve limb topology, head-to-body ratio, body volume, palette, material, and lighting in every frame. Avoid repeated or near-repeated poses, eye-only changes, geometric-only variations, inert loops, accidental stock gestures, and poses whose meaning depends on the prompt.
- Define support/contact logic before generation. Motion must remain physically readable even for soft, stylized, or jointless anatomy.

## States

### idle

Six calm low-distraction poses with a small but visible full-body breathing/settling loop. Frame 0 must work as the reduced-motion still. Each later frame needs a distinct posture beat; eye direction alone is insufficient. No greeting, locomotion, work, review, dramatic emotion, large silhouette change, or new prop.

### running-right

Eight right-facing locomotion poses with readable contact, compression, passing, flight, opposite-contact, and recovery phases. A flight frame must be supported by leg shapes and adjacent phases that make it read as running rather than hovering. Convey travel through body and limbs only. No speed lines, dust, trail, shadow, or floor.

### running-left

Eight left-facing locomotion poses with the same explicit support/contact phases and anatomy gate. Generate independently when mirroring would swap meaningful markings, lighting, text-like details, or an asymmetric prop.

### waving

Four useful poses: settled greeting start, readable lift, clear friendly wave accent, and partial return that flows back to frame 0 without duplicating it. Adapt the gesture to the character's anatomy; do not force a human elbow or wrist onto a jointless limb. Show the wave with the limb only—no arcs, marks, sparkles, or symbols.

### jumping

Five poses: compressed anticipation, extension/lift, clearly airborne peak at unchanged model scale, descent preparation, and landing absorption. Vertical translation without posture change does not pass. Preserve framing and head/body proportions throughout. Show height through body position only—no shadow, dust, impact burst, floor, or landing mark.

### failed

Eight readable but restrained error or deflated reaction poses with controlled recovery into a loop. Use a lowered center of mass, inward arm shape, drooped head, softened legs, and sad or worried eye shape without changing head size, body volume, or limb anatomy. Avoid accidental kneeling, fused limbs, or a collapsing model. The emotional reaction must read before facial detail and must not resemble calm idle, patient waiting, or analytical review. Any tear, star, or smoke-like detail must remain opaque and physically attached; prefer no effect over a detached component.

### waiting

Six expectant poses for approval, help, or user input. Use an upright or slightly forward body, a character-native open/requesting shape, attention toward the user, and a patient hold between visible hopeful checks. Every limb must remain separately readable; do not turn hands or arm ends into vague fused blobs. It must read as paused for someone else, not calmly self-contained like idle, busy like running/work, or critically inspecting like review. If the character cannot express user-facing expectancy with its anatomy at thumbnail size, use one simple capability-audited state cue rather than relying on eye-only motion.

### running

Six focused work/processing poses built around one recognizable character-native action, chosen before generation. Use a quick repeatable rhythm around a stable planted base, with each limb following a continuous path. Do not use generic cross-body arm swapping, dabbing, random scanning, or unexplained choreography. It must look actively busy rather than paused like waiting or slowly scrutinizing like review. Preserve canonical palette, material, and lighting. This is not locomotion. If the character lacks a credible work interaction, authorize one simple stable work surface or prop during semantic design; do not expect generic head sways to communicate work.

### review

Six slow, deliberate inspecting poses with a clear beginning, scrutiny beat, reconsideration, and return. Use a sustained asymmetrical lean, tracking gaze toward a visible target, head tilt, and one restrained character-native evaluative limb position. Do not merely repeat a generic hand-on-chin pose. It must read as checking completed output, not rapid active processing, open-ended waiting, or emotional failure. If no target or anatomy makes inspection legible at thumbnail size, use one simple semantic-design-approved inspection target; do not rely on a downcast gaze that could be mistaken for sadness.
