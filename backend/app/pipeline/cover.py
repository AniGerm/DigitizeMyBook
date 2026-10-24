from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..models import BookMetadata, PageResult

COVER_WIDTH = 1200
COVER_HEIGHT = 1800


def _font_paths() -> tuple[Path, Path]:
    regular = Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf")
    bold = Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf")
    if not regular.exists():
        regular = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
        bold = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    return regular, bold


def _load_font(path: Path, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if path.exists():
        return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _wrap_title(draw: ImageDraw.ImageDraw, title: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    words = title.split()
    if not words:
        return [title or "Unbenanntes Buch"]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        box = draw.textbbox((0, 0), trial, font=font)
        if box[2] - box[0] <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def render_typographic_cover_jpeg(title: str, author: str = "") -> bytes:
    regular_path, bold_path = _font_paths()
    image = Image.new("RGB", (COVER_WIDTH, COVER_HEIGHT), (244, 234, 216))
    draw = ImageDraw.Draw(image)
    margin = 120
    draw.rectangle(
        (margin, margin, COVER_WIDTH - margin, COVER_HEIGHT - margin),
        outline=(143, 61, 43),
        width=4,
    )
    title_font = _load_font(bold_path, 64)
    author_font = _load_font(regular_path, 36)
    max_text = COVER_WIDTH - 2 * margin - 80
    lines = _wrap_title(draw, (title or "Unbenanntes Buch").strip(), title_font, max_text)
    line_heights = []
    for line in lines:
        box = draw.textbbox((0, 0), line, font=title_font)
        line_heights.append(box[3] - box[1])
    author_line = (author or "").strip()
    author_h = 0
    if author_line:
        box = draw.textbbox((0, 0), author_line, font=author_font)
        author_h = box[3] - box[1] + 48
    total_h = sum(line_heights) + max(0, len(lines) - 1) * 14 + author_h
    y = (COVER_HEIGHT - total_h) // 2
    for line, lh in zip(lines, line_heights):
        box = draw.textbbox((0, 0), line, font=title_font)
        w = box[2] - box[0]
        draw.text(((COVER_WIDTH - w) // 2, y), line, fill=(42, 33, 24), font=title_font)
        y += lh + 14
    if author_line:
        box = draw.textbbox((0, 0), author_line, font=author_font)
        w = box[2] - box[0]
        draw.text(((COVER_WIDTH - w) // 2, y + 24), author_line, fill=(92, 76, 60), font=author_font)
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=90)
    return buffer.getvalue()


def page_image_path(pages: list[PageResult], page_index: int) -> Path | None:
    for page in pages:
        if page.index == page_index and not page.skipped:
            path = Path(page.image_path)
            if path.exists():
                return path
    return None


def _jpeg_bytes_from_image_path(path: Path) -> bytes:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return path.read_bytes()
    with Image.open(path) as img:
        buffer = io.BytesIO()
        img.convert("RGB").save(buffer, "JPEG", quality=88)
        return buffer.getvalue()


def resolve_cover_jpeg(metadata: BookMetadata, job_root: Path, pages: list[PageResult]) -> bytes | None:
    mode = getattr(metadata, "cover_mode", "typographic") or "typographic"
    if mode == "image":
        custom = metadata.cover_path
        if custom:
            path = Path(custom)
            if path.exists():
                return _jpeg_bytes_from_image_path(path)
        return render_typographic_cover_jpeg(metadata.title, metadata.author)
    if mode == "page":
        path = page_image_path(pages, metadata.cover_page)
        if path:
            return _jpeg_bytes_from_image_path(path)
        return render_typographic_cover_jpeg(metadata.title, metadata.author)
    return render_typographic_cover_jpeg(metadata.title, metadata.author)
