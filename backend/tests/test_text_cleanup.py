from __future__ import annotations

import unittest

from app.pipeline.text_cleanup import cleanup_german_ocr


class TextCleanupTests(unittest.TestCase):
    def test_splits_merged_words(self):
        self.assertEqual(
            cleanup_german_ocr("ovieleHausaufaben"),
            "oviele Hausaufaben",
        )

    def test_adds_space_after_punctuation(self):
        self.assertEqual(
            cleanup_german_ocr("müsse,als sie"),
            "müsse, als sie",
        )

    def test_normalizes_angle_quotes(self):
        self.assertEqual(cleanup_german_ocr("<< Harry >>"), "« Harry »")
        self.assertEqual(cleanup_german_ocr("< < hallo > >"), "« hallo »")

    def test_latex_angle_quotes_from_ocr(self):
        raw = r'» Ich habe $ \rangle $bitte $ \langle $ gemeint! «'
        fixed = cleanup_german_ocr(raw)
        self.assertIn("»bitte«", fixed.replace(" ", ""))
        self.assertNotIn("$", fixed)
        self.assertNotIn("rangle", fixed)

    def test_angle_brackets_around_letter(self):
        self.assertEqual(
            cleanup_german_ocr("DAS WORT MIT >Z< KOMMT"),
            "DAS WORT MIT »Z« KOMMT",
        )


if __name__ == "__main__":
    unittest.main()
