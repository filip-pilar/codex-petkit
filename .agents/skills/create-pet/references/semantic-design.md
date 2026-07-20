# Semantic state design

Do this before generating any standard animation pixels. A row is not ready for generation because its prompt sounds plausible; it is ready only when an unlabeled viewer could recognize it at the pet's display size.

For every standard state, write a semantic signature with:

- the intended user-facing meaning in observable language;
- a primary silhouette that remains readable at 192×208 and at the UI thumbnail size;
- body rhythm, energy, holds, and loop shape;
- a gaze target and a visible spatial relationship to that target;
- ordered key-pose beats and the decisive frame contribution of each beat;
- the states most likely to be confused with it;
- one concrete cue that separates it from each confusion state;
- the character capability used to express it: body, appendages, eyes, stable prop, or environment;
- a named prop/effect decision: none, or one controlled attached/state-local object that is necessary for recognition.

Run a capability audit before prompting. If the character has no mouth, hands, meaningful appendages, or usable interaction surface, do not ask subtle eye motion to carry several abstract states. Choose a stronger body silhouette or explicitly authorize one simple, stable prop. Decorative text, punctuation, detached symbols, and effects are not semantic substitutes.

The generation prompt describes observable action and silhouette first. The internal row name is metadata, not evidence. Include the anti-confusion cues and the small-size requirement in the prompt. A row whose meaning depends on reading the prompt fails concept design before generation.

Generate a small key-pose concept sheet first. Present it without labels and test recognition against the candidate state list. Only a concept that passes anonymous recognition at both full and UI size may become a full animation strip.

Persist the sheet at `qa/key-pose-concepts.png` and its independent verdict at `qa/key-pose-review.json`. The verdict must record `schema_version: 1`, a distinct `reviewer_id`, `reviewer_independent: true`, `pass: true`, full/UI-size inputs, `prompts_or_motion_plan_seen: false`, and a passing recognition result for every standard state.
