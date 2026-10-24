from __future__ import annotations

import unittest

from app.models import Block, Chapter, PageResult
from app.pipeline.ingest import natural_key
from app.pipeline.structure import (
    detect_chapters,
    flow_paragraphs_from_text,
    is_section_title,
    merge_all_chapters,
    polish_book_line,
    repair_hyphenation,
)
from pathlib import Path


class HyphenationTests(unittest.TestCase):
    def test_joins_line_end_hyphens(self):
        text = "Die Werk-\nstatt war still."
        self.assertEqual(repair_hyphenation(text), "Die Werkstatt war still.")

    def test_adds_space_after_period(self):
        self.assertEqual(
            repair_hyphenation("GPU-OCR.Die Werkstatt war still."),
            "GPU-OCR. Die Werkstatt war still.",
        )

    def test_keeps_paragraphs(self):
        text = "Erster Absatz.\n\nZweiter\nAbsatz."
        self.assertEqual(repair_hyphenation(text), "Erster Absatz.\n\nZweiter Absatz.")

    def test_preserves_intentional_line_breaks(self):
        dialogue = "«Hallo», sagte er.\n«Na und?», antwortete sie."
        self.assertEqual(
            repair_hyphenation(dialogue, preserve_line_breaks=True),
            dialogue,
        )

    def test_flow_splits_book_lines(self):
        text = "Erzählung.\n» Hallo « , sagte er.\n» Na und? «"
        parts = flow_paragraphs_from_text(text)
        self.assertEqual(len(parts), 3)
        self.assertTrue(parts[1].startswith("»"))

    def test_drops_duplicate_ocr_tail_line(self):
        text = (
            "damit sie niemandem Botschaften überbringen konnte.\n"
            "ten überbringen konnte.\n"
            "Harry sah ganz anders aus."
        )
        parts = flow_paragraphs_from_text(text)
        self.assertEqual(len(parts), 2)
        self.assertNotIn("ten überbringen", parts[1])

    def test_joins_lines_across_page_break_phrase(self):
        from app.pipeline.structure import _append_paragraph_item, FlowItem

        items: list = []
        _append_paragraph_item(items, "…in ihren")
        items.append(FlowItem(kind="page_break"))
        _append_paragraph_item(items, "Augen war es eine Schande.")
        self.assertEqual(len(items), 1)
        self.assertIn("in ihren Augen", items[0].text)

    def test_polish_fixes_ocr_guillemets_and_hyphens(self):
        line = "» Schon das dritte Mal « , polterte er. raus- lassen"
        polished = polish_book_line(line)
        self.assertIn("»Schon", polished)
        self.assertIn("«,", polished)
        self.assertIn("rauslassen", polished)

    def test_chapter_html_one_paragraph_per_line(self):
        from app.pipeline.structure import chapter_html

        pages = [
            PageResult(
                index=0,
                source_path="a",
                image_path="a.png",
                blocks=[Block(label="text", content="Zeile eins.\n» Dialog «\nZeile drei.")],
            )
        ]
        chapter = Chapter(id="chapter-1", title="Test", start_page=0, end_page=0)
        html = chapter_html(pages, chapter)
        self.assertEqual(html.count("<p class="), 3)
        self.assertIn('class="dialogue"', html)


class ChapterTests(unittest.TestCase):
    def test_detects_heading_blocks(self):
        pages = [
            PageResult(
                index=0,
                source_path="a",
                image_path="a.png",
                blocks=[Block(label="text", content="Vorspann")],
            ),
            PageResult(
                index=1,
                source_path="b",
                image_path="b.png",
                blocks=[Block(label="paragraph_title", content="Kapitel 1 Anfang")],
            ),
            PageResult(
                index=2,
                source_path="c",
                image_path="c.png",
                blocks=[Block(label="text", content="Fortsetzung")],
            ),
            PageResult(
                index=3,
                source_path="d",
                image_path="d.png",
                blocks=[Block(label="paragraph_title", content="Kapitel 2 Nacht")],
            ),
        ]
        chapters = detect_chapters(pages)
        self.assertGreaterEqual(len(chapters), 2)
        self.assertEqual(chapters[0].title, "Anfang")
        self.assertEqual(chapters[-1].title, "Kapitel 2 Nacht")

    def test_natural_sort(self):
        names = [Path("seite_10.png"), Path("seite_2.png"), Path("seite_1.png")]
        ordered = sorted(names, key=natural_key)
        self.assertEqual([p.name for p in ordered], ["seite_1.png", "seite_2.png", "seite_10.png"])

    def test_rejects_dialogue_fragment_as_section_title(self):
        page = PageResult(index=1, source_path="a", image_path="a.png", width=2400, height=3200)
        self.assertFalse(
            is_section_title("Lächeln-Preises der Hexenwoche. «", [255, 818, 1315, 939], page)
        )

    def test_rejects_narrative_line_fragment(self):
        page = PageResult(index=0, source_path="a", image_path="a.png", width=1654, height=2339)
        self.assertFalse(
            is_section_title(
                "Im Ligusterweg Nummer 4 war mal wieder bereits beim",
                [255, 547, 1410, 617],
                page,
            )
        )

    def test_accepts_real_section_title(self):
        page = PageResult(index=0, source_path="a", image_path="a.png", width=2400, height=3200)
        self.assertTrue(
            is_section_title("Der sehr geheime Taschenkalender", [473, 71, 2763, 355], page)
        )
        self.assertTrue(
            is_section_title("Ein grässlicher Geburtstag", [420, 358, 1980, 451], page)
        )

    def test_sort_pages_by_printed_page(self):
        from app.pipeline.preprocess import sort_pages_by_printed_page

        pages = [
            PageResult(index=0, source_path="a", image_path="a.png", printed_page=236),
            PageResult(index=1, source_path="b", image_path="b.png", printed_page=237),
            PageResult(index=2, source_path="c", image_path="c.png", printed_page=240),
            PageResult(index=3, source_path="d", image_path="d.png", printed_page=241),
            PageResult(index=4, source_path="e", image_path="e.png", printed_page=239),
        ]
        ordered = sort_pages_by_printed_page(pages)
        self.assertEqual([p.printed_page for p in ordered], [236, 237, 239, 240, 241])

    def test_detects_upside_down_line_order(self):
        from app.pipeline.ocr import _lines_read_bottom_up
        from app.models import Block

        blocks = [
            Block(label="text", content="einfach durchfliegen, oder? «", bbox=[0, 20, 100, 40]),
            Block(label="text", content="noch eine Zeile.", bbox=[0, 60, 100, 80]),
            Block(label="text", content="und noch eine.", bbox=[0, 100, 100, 120]),
            Block(label="text", content="Sie steckten die Köpfe um die Ecke.", bbox=[0, 900, 100, 920]),
        ]
        self.assertTrue(_lines_read_bottom_up(blocks))

    def test_collapse_section_title_chapter_split(self):
        pages = [
            PageResult(
                index=0,
                source_path="a",
                image_path="a.png",
                width=2400,
                height=3200,
                blocks=[
                    Block(
                        label="paragraph_title",
                        content="Der sehr geheime Taschenkalender",
                        bbox=[473, 71, 2763, 355],
                    ),
                    Block(label="text", content="Hermine blieb im Krankenflügel."),
                ],
            ),
            PageResult(index=1, source_path="b", image_path="b.png", blocks=[Block(label="text", content="Fortsetzung")]),
            PageResult(index=2, source_path="c", image_path="c.png", blocks=[Block(label="text", content="Ende")]),
        ]
        chapters = [
            Chapter(id="chapter-1", title="Der sehr geheime Taschenkalender", start_page=0, end_page=0),
            Chapter(id="chapter-2", title="Teil 2", start_page=1, end_page=2),
        ]
        from app.pipeline.structure import _collapse_section_title_splits

        merged = _collapse_section_title_splits(pages, chapters, "HP Test")
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].start_page, 0)
        self.assertEqual(merged[0].end_page, 2)

    def test_merge_all_chapters(self):
        chapters = [
            Chapter(id="chapter-1", title="A", start_page=0, end_page=9),
            Chapter(id="chapter-2", title="B", start_page=10, end_page=19),
        ]
        merged = merge_all_chapters(chapters, "Ganzes Buch")
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].end_page, 19)


if __name__ == "__main__":
    unittest.main()
