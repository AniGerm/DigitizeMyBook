from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from app.models import Block, BookMetadata, Chapter, PageResult
from app.pipeline.export_epub import export_epub
from app.pipeline.export_pdf import export_pdf
from app.pipeline.ingest import ingest
from app.pipeline.preprocess import preprocess_pages
from app.pipeline.structure import merge_chapters, split_chapter


def _page(index: int, path: str, title: str | None = None, text: str = "Hallo Welt") -> PageResult:
    blocks = []
    if title:
        blocks.append(Block(label="paragraph_title", content=title, bbox=[20, 20, 200, 50]))
    blocks.append(Block(label="text", content=text, bbox=[20, 80, 300, 140]))
    return PageResult(index=index, source_path=path, image_path=path, width=400, height=500, blocks=blocks)


class ExportTests(unittest.TestCase):
    def test_epub_and_pdf_and_chapter_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            images = []
            for i in range(3):
                img = Image.new("RGB", (400, 500), "white")
                draw = ImageDraw.Draw(img)
                draw.text((30, 40), f"Seite {i+1}", fill="black")
                path = tmp_path / f"p{i}.png"
                img.save(path)
                images.append(path)
            pages = [
                _page(0, str(images[0]), "Kapitel 1 Start", "Einleitungstext"),
                _page(1, str(images[1]), None, "Mehr Text"),
                _page(2, str(images[2]), "Kapitel 2 Ende", "Schluss"),
            ]
            chapters = [
                Chapter(id="chapter-1", title="Kapitel 1 Start", start_page=0, end_page=1),
                Chapter(id="chapter-2", title="Kapitel 2 Ende", start_page=2, end_page=2),
            ]
            meta = BookMetadata(title="Testbuch", author="Tester", language="de", cover_path=str(images[0]))
            epub_path = export_epub(tmp_path / "book.epub", pages, chapters, meta)
            pdf_path = export_pdf(tmp_path / "book.pdf", pages, meta, chapters)
            self.assertTrue(epub_path.exists() and epub_path.stat().st_size > 100)
            self.assertTrue(pdf_path.exists() and pdf_path.stat().st_size > 100)
            from pypdf import PdfReader

            reader = PdfReader(str(pdf_path))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
            self.assertIn("Einleitungstext", text)
            self.assertIn("Testbuch", text)
            merged = merge_chapters(chapters, "chapter-1")
            self.assertEqual(len(merged), 1)
            self.assertEqual(merged[0].end_page, 2)
            split = split_chapter(chapters, "chapter-1", 1)
            self.assertEqual(len(split), 3)
            self.assertEqual(split[0].end_page, 0)
            self.assertEqual(split[1].start_page, 1)

    def test_ingest_and_preprocess(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "src"
            src.mkdir()
            for name, color in (("seite_10.png", "white"), ("seite_2.png", "black")):
                Image.new("RGB", (320, 400), color).save(src / name)
            pages = ingest([str(src)], tmp_path / "raw").pages
            self.assertEqual(len(pages), 2)
            self.assertTrue(pages[0].image_path.endswith("0000.png"))
            processed = preprocess_pages(pages, tmp_path / "prep", enable=True, skip_blank=True)
            self.assertEqual(len(processed), 2)
            self.assertTrue(processed[0].skipped)

    def test_rotates_sideways_text_and_splits_spread(self):
        import cv2
        import numpy as np

        from app.pipeline.preprocess import make_upright, split_spread

        canvas = np.full((200, 480, 3), 255, np.uint8)
        for y in range(30, 170, 22):
            canvas[y : y + 8, 20:220] = 0
            canvas[y : y + 8, 260:460] = 0
        canvas[:, 236:244] = 40
        sideways = cv2.rotate(canvas, cv2.ROTATE_90_CLOCKWISE)
        upright = make_upright(sideways)
        self.assertGreater(upright.shape[1], upright.shape[0])
        parts = split_spread(upright)
        self.assertEqual(len(parts), 2)

    def test_portrait_page_is_not_split(self):
        import cv2
        import numpy as np

        from app.pipeline.preprocess import is_likely_spread, split_spread

        page = np.full((2400, 1800, 3), 255, np.uint8)
        for y in range(120, 2200, 34):
            page[y : y + 10, 120:1680] = 0
        page[900:940, 120:1680] = 255
        self.assertFalse(is_likely_spread(page))
        self.assertEqual(len(split_spread(page)), 1)

    def test_flips_upside_down_german_text(self):
        import cv2
        import numpy as np

        from app.pipeline.preprocess import _correct_180, _diacritic_upright_score

        page = np.full((420, 320, 3), 255, np.uint8)
        for x in range(40, 280, 24):
            for y in range(50, 370, 36):
                page[y : y + 16, x : x + 9] = 0
                page[y - 7 : y - 3, x + 3 : x + 7] = 0
        inverted = cv2.rotate(page, cv2.ROTATE_180)
        self.assertGreater(_diacritic_upright_score(page), 0.5)
        self.assertLess(_diacritic_upright_score(inverted), -0.5)
        restored = _correct_180(inverted)
        self.assertGreater(_diacritic_upright_score(restored), 0.5)

    def test_scanner_bar_is_trimmed_despite_specks(self):
        import numpy as np

        from app.pipeline.preprocess import _trim_to_paper

        page = np.full((400, 300, 3), 230, np.uint8)
        page[:, :12] = 255
        page[:, 12:80] = 8
        page[40:48, 20:28] = 255
        page[200:208, 30:40] = 200
        trimmed = _trim_to_paper(page)
        self.assertLess(trimmed.shape[1], 250)
        self.assertGreater(float(trimmed.mean()), 180)

    def test_split_halves_swap_when_right_page_comes_first(self):
        import numpy as np

        from app.pipeline.preprocess import order_spread_halves, reorder_spread_pages

        left_page = np.full((200, 120, 3), 230, np.uint8)
        left_page[:, :18] = 10
        right_page = np.full((200, 120, 3), 230, np.uint8)
        right_page[:, -18:] = 10
        ordered = order_spread_halves(right_page, left_page)
        self.assertLess(float(ordered[0][:, :18].mean()), 40)
        self.assertLess(float(ordered[1][:, -18:].mean()), 40)

        first = PageResult(index=0, source_path="a", image_path="a", spread_id=3, printed_page=7)
        second = PageResult(index=1, source_path="a", image_path="a", spread_id=3, printed_page=6)
        reordered = reorder_spread_pages([first, second])
        self.assertEqual([p.printed_page for p in reordered], [6, 7])
        self.assertEqual([p.index for p in reordered], [0, 1])

    def test_black_bar_image_block_is_dropped(self):
        from app.pipeline.ocr import _extract_blocks

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            img = Image.new("RGB", (400, 600), (235, 230, 210))
            draw = ImageDraw.Draw(img)
            draw.rectangle((0, 0, 70, 600), fill=(5, 5, 5))
            path = tmp_path / "page.png"
            img.save(path)
            page = PageResult(index=0, source_path=str(path), image_path=str(path), width=400, height=600)
            blocks = _extract_blocks(
                {
                    "parsing_res_list": [
                        {
                            "block_label": "image",
                            "block_content": "",
                            "block_bbox": [2, 4, 68, 590],
                            "block_order": 0,
                        },
                        {
                            "block_label": "text",
                            "block_content": "Hallo",
                            "block_bbox": [90, 40, 300, 80],
                            "block_order": 1,
                        },
                    ]
                },
                page,
                tmp_path / "images",
            )
            self.assertEqual(len(blocks), 1)
            self.assertEqual(blocks[0].content, "Hallo")
            self.assertFalse(list((tmp_path / "images").glob("*.jpg")))


class NumpyOcrTests(unittest.TestCase):
    def test_numpy_rec_boxes_do_not_crash(self):
        import numpy as np

        from app.pipeline.ocr import _extract_blocks

        page = PageResult(index=0, source_path="x", image_path="x.png", width=10, height=10)
        data = {
            "rec_texts": np.array(["Hallo Welt"]),
            "rec_boxes": np.array([[1.0, 2.0, 30.0, 40.0]]),
        }
        blocks = _extract_blocks(data, page, Path("/tmp"))
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].content, "Hallo Welt")
        self.assertEqual(blocks[0].bbox[:4], [1.0, 2.0, 30.0, 40.0])


class UploadTests(unittest.TestCase):
    def test_store_uploads_keeps_selection_order(self):
        import os

        from app.uploads import store_uploads

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["XDG_DATA_HOME"] = tmp
            first = Image.new("RGB", (40, 50), "white")
            second = Image.new("RGB", (40, 50), "black")
            from io import BytesIO

            buf1, buf2 = BytesIO(), BytesIO()
            first.save(buf1, "PNG")
            second.save(buf2, "JPEG")
            folder, skipped = store_uploads(
                [
                    ("page-b.png", buf1.getvalue(), "image/png"),
                    ("page-a.jpg", buf2.getvalue(), "image/jpeg"),
                ]
            )
            self.assertEqual(skipped, 0)
            names = sorted(p.name for p in folder.iterdir())
            self.assertEqual(names, ["0000.png", "0001.jpg"])
            result = ingest([str(folder)], Path(tmp) / "raw")
            self.assertEqual(len(result.pages), 2)


if __name__ == "__main__":
    unittest.main()
