from __future__ import annotations

from pathlib import Path

from ebooklib import epub
from slugify import slugify

from ..models import BookMetadata, Chapter, PageResult
from .cover import resolve_cover_jpeg
from .structure import PathRel, chapter_html


COVER_XHTML_BODY = (
    '<div style="margin:0;padding:0;text-align:center;">'
    '<img src="cover.jpg" alt="" style="width:100%;max-height:100vh;object-fit:contain;"/>'
    "</div>"
)

STYLESHEET = """
body { font-family: "Palatino Linotype", Palatino, "Book Antiqua", Georgia, serif; line-height: 1.45; margin: 1.2em; }
h1 { font-size: 1.6em; margin: 0 0 1em 0; page-break-before: always; }
body > h1:first-child { page-break-before: auto; }
h2 { font-size: 1.2em; margin: 1.2em 0 0.6em 0; }
p.prose { margin: 0 0 0.45em 0; text-align: justify; }
p.dialogue { margin: 0.15em 0 0.35em 0; text-align: left; }
.figure { text-align: center; }
.figure img { max-width: 100%; height: auto; }
.caption { font-size: 0.9em; text-align: center; }
"""


def export_epub(
    dest: Path,
    pages: list[PageResult],
    chapters: list[Chapter],
    metadata: BookMetadata,
    *,
    job_root: Path | None = None,
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    book = epub.EpubBook()
    book.set_identifier(f"digitizemybook-{slugify(metadata.title)}")
    book.set_title(metadata.title or "Unbenanntes Buch")
    book.set_language(metadata.language or "de")
    if metadata.author:
        book.add_author(metadata.author)

    css = epub.EpubItem(uid="style", file_name="style/book.css", media_type="text/css", content=STYLESHEET)
    book.add_item(css)

    used_images: dict[str, str] = {}
    for page in pages:
        for block in page.blocks:
            if not block.image_path:
                continue
            src = Path(block.image_path)
            if not src.exists():
                continue
            rel = PathRel(block.image_path)
            if rel in used_images:
                continue
            media = "image/jpeg" if src.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
            item = epub.EpubItem(uid=f"img-{len(used_images)}", file_name=rel, media_type=media, content=src.read_bytes())
            book.add_item(item)
            used_images[rel] = rel

    root = job_root or dest.parent
    cover_bytes = resolve_cover_jpeg(metadata, root, pages)
    cover_page = None
    if cover_bytes:
        book.set_cover("cover.jpg", cover_bytes)
        for item in book.get_items():
            if isinstance(item, epub.EpubCoverHtml):
                item.content = COVER_XHTML_BODY
                cover_page = item
                break

    spine: list = []
    if cover_page is not None:
        spine.append(cover_page)
    spine.append("nav")
    toc = []
    for chapter in chapters:
        html = chapter_html(pages, chapter)
        item = epub.EpubHtml(
            title=chapter.title,
            file_name=f"{chapter.id}.xhtml",
            lang=metadata.language or "de",
        )
        item.content = html
        item.add_item(css)
        book.add_item(item)
        spine.append(item)
        toc.append(item)

    book.toc = toc
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = spine
    epub.write_epub(str(dest), book)
    return dest
