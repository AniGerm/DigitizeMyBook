from __future__ import annotations

import unittest

from app.models import PageResult
from app.pipeline.page_numbers import (
    is_margin_page_hint,
    normalize_page_hint,
    parse_printed_page_number,
    pick_printed_page,
)


class PageNumberTests(unittest.TestCase):
    def test_plain_numbers(self):
        for text in ("352", "353", "554", "244", "262"):
            self.assertEqual(parse_printed_page_number(text), int(text))

    def test_seite_von_formats(self):
        self.assertEqual(parse_printed_page_number("Seite 1 von 6"), 1)
        self.assertEqual(parse_printed_page_number("Seite 2 von 6"), 2)
        self.assertEqual(parse_printed_page_number("seite 3/6"), 3)
        self.assertEqual(parse_printed_page_number("SEITE 4 OF 12"), 4)

    def test_ocr_typos_for_von(self):
        self.assertEqual(parse_printed_page_number("Seite 1 cln 6"), 1)
        self.assertEqual(parse_printed_page_number("Seite 2 uon 6"), 2)

    def test_prefix_formats(self):
        self.assertEqual(parse_printed_page_number("S. 352"), 352)
        self.assertEqual(parse_printed_page_number("S.352"), 352)
        self.assertEqual(parse_printed_page_number("Bl. 12"), 12)
        self.assertEqual(parse_printed_page_number("Page 554"), 554)

    def test_fraction_formats(self):
        self.assertEqual(parse_printed_page_number("352 / 554"), 352)
        self.assertEqual(parse_printed_page_number("1/6"), 1)

    def test_decorated_numbers(self):
        self.assertEqual(parse_printed_page_number("- 352 -"), 352)
        self.assertEqual(parse_printed_page_number("— 353 —"), 353)

    def test_rejects_body_text(self):
        self.assertIsNone(parse_printed_page_number("Harry war 12 Jahre alt"))
        self.assertIsNone(parse_printed_page_number("Kapitel 3"))

    def test_margin_detection(self):
        page = PageResult(index=0, source_path="a", image_path="a", width=1000, height=1000)
        self.assertTrue(is_margin_page_hint([450, 900, 550, 930], page, "352"))
        self.assertFalse(is_margin_page_hint([450, 500, 550, 530], page, "352"))
        self.assertTrue(
            is_margin_page_hint([300, 820, 700, 860], page, "Seite 2 von 6"),
        )

    def test_pick_best_candidate(self):
        page = PageResult(index=0, source_path="a", image_path="a", width=1000, height=1000)
        chosen = pick_printed_page(
            [
                ("352", [450, 900, 550, 930], page),
                ("Seite 2 von 6", [300, 880, 700, 920], page),
            ]
        )
        self.assertEqual(chosen, 2)

    def test_normalize_page_hint(self):
        self.assertEqual(normalize_page_hint("Seite  1   cln   6"), "Seite 1 von 6")


if __name__ == "__main__":
    unittest.main()
