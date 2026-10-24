from __future__ import annotations

import re
import tempfile
import unittest
import zipfile
from pathlib import Path

from app.models import BookMetadata, Chapter, PageResult
from app.pipeline.export_epub import export_epub


class ExportEpubCoverTests(unittest.TestCase):
    def test_cover_in_spine_and_line_formatting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pages = [
                PageResult(
                    index=0,
                    source_path="a",
                    image_path="a.png",
                    blocks=[{"label": "text", "content": "Zeile eins.\n» Dialog «", "bbox": []}],
                )
            ]
            # fix blocks as Block objects
            from app.models import Block

            pages[0].blocks = [Block(label="text", content="Zeile eins.\n» Dialog «")]
            meta = BookMetadata(title="T", author="A", cover_mode="typographic")
            out = root / "book.epub"
            export_epub(out, pages, [Chapter(id="chapter-1", title="K1", start_page=0, end_page=0)], meta, job_root=root)
            with zipfile.ZipFile(out) as z:
                opf = z.read([n for n in z.namelist() if n.endswith("content.opf")][0]).decode()
                self.assertIn('idref="cover"', opf)
                ch = z.read([n for n in z.namelist() if "chapter-1" in n][0]).decode()
                self.assertIn('class="dialogue"', ch)


if __name__ == "__main__":
    unittest.main()
