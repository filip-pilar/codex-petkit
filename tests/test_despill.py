from __future__ import annotations

import unittest

from PIL import Image, ImageDraw

from petkit.v2scripts import despill_chroma_edges as despill


def synthetic_atlas() -> Image.Image:
    image = Image.new("RGBA", (384, 208), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((8, 12, 170, 190), fill=(15, 220, 90, 230))
    draw.rectangle((192, 20, 375, 180), fill=(30, 180, 80, 255))
    draw.rectangle((0, 0, 191, 4), fill=(30, 220, 80, 180))
    return image


class DespillTests(unittest.TestCase):
    def test_cellwise_processing_matches_full_atlas_processing(self) -> None:
        source = synthetic_atlas()
        full, _ = despill.decontaminate_image(source, chroma_key=(0, 255, 0))
        cellwise, report = despill.decontaminate_atlas(source, chroma_key=(0, 255, 0))
        self.assertEqual(full.tobytes(), cellwise.tobytes())
        self.assertEqual(report["cache_hits"], 0)
        self.assertEqual(report["cache_misses"], 2)

    def test_unchanged_cells_reuse_and_match_a_fresh_processing_pass(self) -> None:
        source = synthetic_atlas()
        previous, previous_report = despill.decontaminate_atlas(source, chroma_key=(0, 255, 0))
        changed = source.copy()
        changed.putpixel((24, 24), (220, 30, 100, 220))

        fresh, _ = despill.decontaminate_atlas(changed, chroma_key=(0, 255, 0))
        reused, report = despill.decontaminate_atlas(
            changed,
            chroma_key=(0, 255, 0),
            previous_raw=source,
            previous_output=previous,
            previous_report=previous_report,
        )

        self.assertEqual(fresh.tobytes(), reused.tobytes())
        self.assertEqual(report["cache_hits"], 1)
        self.assertEqual(report["cache_misses"], 1)


if __name__ == "__main__":
    unittest.main()
