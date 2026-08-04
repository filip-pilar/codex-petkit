# OpenAI hatch-pet provenance

- Repository: https://github.com/openai/skills
- Upstream skill path: `skills/.curated/hatch-pet`
- V2 source snapshot: an installed `hatch-pet` skill snapshot inspected on 2026-07-19 and retained locally before the project-local fork was completed
- Earlier inspected tree: `49f948faa9258a0c61caceaf225e179651397431`
- License: Apache License 2.0; retained in this directory.

The project owns its editable-project model, orchestration, CLI, build lineage, review gates, and installation safety. The following current V2 helper sources are vendored verbatim under `petkit/v2scripts/` so production builds do not depend on a changing global skill:

- `assemble_extended_atlas.py`
- `extract_strip_frames.py`
- `despill_chroma_edges.py`
- `extract_cardinal_anchors.py`
- `compose_cardinal_anchor_strip.py`
- `make_direction_qa_sheet.py`
- `make_direction_blind_qa_sheet.py`
- `measure_direction_continuity.py`
- `combine_direction_blind_verdicts.py`
- `validate_direction_blind_verdicts.py`
- `validate_atlas.py`

`petkit/references/v2/animation-rows.md` is the matching retained format reference. `SHA256SUMS` identifies the exact retained files. The snapshot is provenance only and there is no runtime synchronization with an installed skill.

The project intentionally diverges from the retained `despill_chroma_edges.py` snapshot: processing version 2 preserves the original pixel results while limiting repeated work to pending edge pixels, processing atlas cells with an atlas-wide boundary mask, and reusing cells only when their raw pixels, boundary mask, and parameters match. The divergence is local performance and cacheability work; the parity regression in `tests/test_despill.py` guards the output contract.
