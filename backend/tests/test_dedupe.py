from __future__ import annotations

import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from PIL import Image

from app.models import PageResult
from app.pipeline.dedupe import dedupe_pages, dedupe_upload_bytes
from app.pipeline.ingest import ingest


class DedupeTests(unittest.TestCase):
    def test_dedupe_upload_bytes(self):
        data = b"same-image"
        unique, skipped = dedupe_upload_bytes(
            [
                ("a.jpg", data, "image/jpeg"),
                ("b.jpg", data, "image/jpeg"),
                ("c.jpg", b"other", "image/jpeg"),
            ]
        )
        self.assertEqual(len(unique), 2)
        self.assertEqual(skipped, 1)

    def test_dedupe_pages_by_hash_and_printed_page(self):
        pages = [
            PageResult(index=0, source_path="a", image_path="/tmp/a.png", printed_page=236),
            PageResult(index=1, source_path="b", image_path="/tmp/a.png", printed_page=236),
            PageResult(index=2, source_path="c", image_path="/tmp/c.png", printed_page=237),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            img_a = Image.new("RGB", (20, 20), "white")
            img_b = Image.new("RGB", (20, 20), "black")
            img_a.save(Path(tmp) / "a.png")
            img_b.save(Path(tmp) / "c.png")
            pages[0].image_path = str(Path(tmp) / "a.png")
            pages[1].image_path = str(Path(tmp) / "a.png")
            pages[2].image_path = str(Path(tmp) / "c.png")
            result = dedupe_pages(pages)
            self.assertEqual(result.skipped, 1)
            self.assertEqual(len(result.pages), 2)
            self.assertEqual([p.printed_page for p in result.pages], [236, 237])

    def test_ingest_skips_duplicate_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "src"
            src.mkdir()
            buf = BytesIO()
            Image.new("RGB", (40, 50), "white").save(buf, "PNG")
            data = buf.getvalue()
            (src / "0000.png").write_bytes(data)
            (src / "0001.png").write_bytes(data)
            (src / "0002.png").write_bytes(data)
            Image.new("RGB", (40, 50), "black").save(src / "0003.png")
            result = ingest([str(src)], tmp_path / "raw")
            self.assertEqual(len(result.pages), 2)
            self.assertEqual(result.skipped_duplicates, 2)


if __name__ == "__main__":
    unittest.main()
