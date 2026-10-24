from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.models import BookMetadata, PageResult
from app.pipeline.cover import render_typographic_cover_jpeg, resolve_cover_jpeg


class CoverTests(unittest.TestCase):
    def test_typographic_cover_has_bytes(self):
        data = render_typographic_cover_jpeg("Harry Potter", "J.K. Rowling")
        self.assertGreater(len(data), 500)
        self.assertTrue(data.startswith(b"\xff\xd8"))

    def test_resolve_page_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "page.png"
            from PIL import Image

            Image.new("RGB", (100, 100), "red").save(path)
            pages = [PageResult(index=2, source_path="x", image_path=str(path))]
            meta = BookMetadata(title="T", cover_mode="page", cover_page=2)
            data = resolve_cover_jpeg(meta, Path(tmp), pages)
            self.assertGreater(len(data), 100)

    def test_image_mode_keeps_jpeg_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            jpg = root / "cover_custom.jpg"
            payload = b"\xff\xd8\xff\xe0" + b"x" * 200 + b"\xff\xd9"
            jpg.write_bytes(payload)
            meta = BookMetadata(title="T", cover_mode="image", cover_path=str(jpg))
            self.assertEqual(resolve_cover_jpeg(meta, root, []), payload)


if __name__ == "__main__":
    unittest.main()
