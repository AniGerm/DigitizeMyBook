from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..models import PageResult, SourcePlanEntry
from .dedupe import content_hash, image_file_hash

IngestProgress = Callable[[str, int, int], None]


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp", ".heic", ".heif"}
PDF_EXT = ".pdf"


@dataclass
class IngestResult:
    pages: list[PageResult]
    skipped_duplicates: int = 0


def natural_key(path: Path) -> list:
    parts: list = []
    for token in re.split(r"(\d+)", path.as_posix()):
        if token.isdigit():
            parts.append(int(token))
        else:
            parts.append(token.lower())
    return parts


def collect_input_files(sources: list[str]) -> list[Path]:
    files: list[Path] = []
    for raw in sources:
        path = Path(raw).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"Pfad nicht gefunden: {path}")
        if path.is_dir():
            found = [
                p
                for p in path.rglob("*")
                if p.is_file() and p.suffix.lower() in IMAGE_EXTS | {PDF_EXT}
            ]
            files.extend(sorted(found, key=natural_key))
        elif path.suffix.lower() in IMAGE_EXTS | {PDF_EXT}:
            files.append(path)
        else:
            raise ValueError(f"Nicht unterstützte Datei: {path}")
    if not files:
        raise ValueError("Keine Bilder oder PDFs in der Auswahl gefunden.")
    return files


def pdf_page_count(pdf_path: Path) -> int:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(pdf_path))
    count = len(pdf)
    pdf.close()
    return count


def render_pdf_page(
    pdf_path: Path,
    page_index: int,
    dest_dir: Path,
    dpi: int,
    out_index: int,
) -> PageResult:
    import pypdfium2 as pdfium

    dest_dir.mkdir(parents=True, exist_ok=True)
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        if page_index < 0 or page_index >= len(pdf):
            raise ValueError(f"PDF-Seite außerhalb des Bereichs: {page_index + 1}")
        scale = dpi / 72
        page = pdf[page_index]
        bitmap = page.render(scale=scale)
        image = bitmap.to_pil().convert("RGB")
        out = dest_dir / f"{out_index:04d}.png"
        image.save(out, "PNG")
        result = PageResult(
            index=out_index,
            source_path=str(pdf_path),
            image_path=str(out),
            width=image.width,
            height=image.height,
            source_index=out_index,
        )
        page.close()
        return result
    finally:
        pdf.close()


def copy_image_page(image_path: Path, dest_dir: Path, out_index: int) -> PageResult:
    from PIL import Image

    dest_dir.mkdir(parents=True, exist_ok=True)
    image = Image.open(image_path).convert("RGB")
    out = dest_dir / f"{out_index:04d}.png"
    image.save(out, "PNG")
    return PageResult(
        index=out_index,
        source_path=str(image_path),
        image_path=str(out),
        width=image.width,
        height=image.height,
        source_index=out_index,
    )


def ingest_plan_entries(
    entries: list[SourcePlanEntry],
    dest_dir: Path,
    dpi: int,
    on_progress: IngestProgress | None = None,
) -> list[PageResult]:
    if not entries:
        return []
    dest_dir.mkdir(parents=True, exist_ok=True)
    pages: list[PageResult] = []
    pdf_totals: dict[str, int] = {}

    for offset, entry in enumerate(entries):
        source = Path(entry.source_path)
        if entry.pdf_page is not None:
            if entry.source_path not in pdf_totals:
                pdf_totals[entry.source_path] = pdf_page_count(source)
            total = pdf_totals[entry.source_path]
            if on_progress:
                on_progress(
                    f"PDF {source.name}: Seite {entry.pdf_page + 1} von {total}",
                    entry.pdf_page + 1,
                    total,
                )
            pages.append(
                render_pdf_page(
                    source,
                    entry.pdf_page,
                    dest_dir,
                    dpi,
                    entry.source_index,
                )
            )
            continue
        if on_progress:
            on_progress(
                f"Bild {source.name} ({offset + 1}/{len(entries)})",
                offset + 1,
                len(entries),
            )
        pages.append(copy_image_page(source, dest_dir, entry.source_index))
    return pages


def render_pdf_pages(
    pdf_path: Path,
    dest_dir: Path,
    dpi: int,
    start_index: int,
    *,
    max_pages: int | None = None,
    on_progress: IngestProgress | None = None,
) -> list[PageResult]:
    import pypdfium2 as pdfium

    dest_dir.mkdir(parents=True, exist_ok=True)
    pdf = pdfium.PdfDocument(str(pdf_path))
    scale = dpi / 72
    total = len(pdf)
    limit = total if max_pages is None else min(total, max_pages)
    pages: list[PageResult] = []
    for i in range(limit):
        page = pdf[i]
        if on_progress:
            on_progress(f"PDF {pdf_path.name}: Seite {i + 1} von {total}", i + 1, total)
        bitmap = page.render(scale=scale)
        image = bitmap.to_pil().convert("RGB")
        index = start_index + i
        out = dest_dir / f"{index:04d}.png"
        image.save(out, "PNG")
        pages.append(
            PageResult(
                index=index,
                source_path=str(pdf_path),
                image_path=str(out),
                width=image.width,
                height=image.height,
            )
        )
        page.close()
    pdf.close()
    return pages


def ingest(
    sources: list[str],
    dest_dir: Path,
    dpi: int = 200,
    max_pages: int | None = None,
    on_progress: IngestProgress | None = None,
) -> IngestResult:
    dest_dir.mkdir(parents=True, exist_ok=True)
    files = collect_input_files(sources)
    pages: list[PageResult] = []
    seen_hashes: set[str] = set()
    skipped_duplicates = 0
    index = 0
    for file in files:
        if max_pages is not None and index >= max_pages:
            break
        if file.suffix.lower() == PDF_EXT:
            remaining = None if max_pages is None else max_pages - index
            rendered = render_pdf_pages(
                file,
                dest_dir,
                dpi,
                index,
                max_pages=remaining,
                on_progress=on_progress,
            )
            for page in rendered:
                digest = image_file_hash(Path(page.image_path))
                if digest and digest in seen_hashes:
                    Path(page.image_path).unlink(missing_ok=True)
                    skipped_duplicates += 1
                    continue
                if digest:
                    seen_hashes.add(digest)
                page.index = index
                pages.append(page)
                index += 1
            continue
        from PIL import Image

        raw = file.read_bytes()
        digest = content_hash(raw)
        if digest in seen_hashes:
            skipped_duplicates += 1
            continue
        seen_hashes.add(digest)

        image = Image.open(file).convert("RGB")
        out = dest_dir / f"{index:04d}.png"
        image.save(out, "PNG")
        pages.append(
            PageResult(
                index=index,
                source_path=str(file),
                image_path=str(out),
                width=image.width,
                height=image.height,
            )
        )
        index += 1
    if not pages:
        raise ValueError("Es konnten keine Seiten geladen werden.")
    return IngestResult(pages=pages, skipped_duplicates=skipped_duplicates)
