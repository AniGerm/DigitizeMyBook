from __future__ import annotations

from pathlib import Path

from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image as RLImage,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)
from PIL import Image as PILImage

from ..models import BookMetadata, Chapter, PageResult
from .structure import chapter_flow


def _font_pair() -> tuple[str, str]:
    regular = Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf")
    bold = Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf")
    if not regular.exists():
        regular = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
        bold = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    if regular.exists():
        try:
            pdfmetrics.getFont("BookSerif")
        except KeyError:
            pdfmetrics.registerFont(TTFont("BookSerif", str(regular)))
            if bold.exists():
                pdfmetrics.registerFont(TTFont("BookSerif-Bold", str(bold)))
            else:
                pdfmetrics.registerFont(TTFont("BookSerif-Bold", str(regular)))
        return "BookSerif", "BookSerif-Bold"
    return "Times-Roman", "Times-Bold"


def _styles() -> dict[str, ParagraphStyle]:
    body_font, heading_font = _font_pair()
    return {
        "title": ParagraphStyle(
            "BookTitle",
            fontName=heading_font,
            fontSize=22,
            leading=28,
            alignment=TA_CENTER,
            spaceAfter=8 * mm,
        ),
        "h1": ParagraphStyle(
            "BookH1",
            fontName=heading_font,
            fontSize=16,
            leading=22,
            alignment=TA_LEFT,
            spaceBefore=10 * mm,
            spaceAfter=5 * mm,
        ),
        "h2": ParagraphStyle(
            "BookH2",
            fontName=heading_font,
            fontSize=13,
            leading=18,
            alignment=TA_LEFT,
            spaceBefore=6 * mm,
            spaceAfter=3 * mm,
        ),
        "body": ParagraphStyle(
            "BookBody",
            fontName=body_font,
            fontSize=11,
            leading=16,
            alignment=TA_JUSTIFY,
            spaceAfter=3.2 * mm,
            firstLineIndent=6 * mm,
        ),
        "caption": ParagraphStyle(
            "BookCaption",
            fontName=body_font,
            fontSize=9,
            leading=12,
            alignment=TA_CENTER,
            spaceAfter=5 * mm,
        ),
        "author": ParagraphStyle(
            "BookAuthor",
            fontName=body_font,
            fontSize=12,
            leading=16,
            alignment=TA_CENTER,
            spaceAfter=16 * mm,
        ),
    }


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br/>")
    )


def export_pdf(
    dest: Path,
    pages: list[PageResult],
    metadata: BookMetadata,
    chapters: list[Chapter] | None = None,
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    styles = _styles()
    document = SimpleDocTemplate(
        str(dest),
        pagesize=A4,
        title=metadata.title or "Unbenanntes Buch",
        author=metadata.author or "",
        leftMargin=22 * mm,
        rightMargin=22 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
    )
    story: list = [
        Paragraph(_escape(metadata.title or "Unbenanntes Buch"), styles["title"]),
    ]
    if metadata.author:
        story.append(Paragraph(_escape(metadata.author), styles["author"]))

    if not chapters:
        chapters = [
            Chapter(
                id="chapter-1",
                title=metadata.title or "Buch",
                start_page=0,
                end_page=max((page.index for page in pages), default=0),
            )
        ]

    usable_width = A4[0] - 44 * mm
    for index, chapter in enumerate(chapters):
        if index:
            story.append(PageBreak())
        for item in chapter_flow(pages, chapter):
            if item.kind == "heading":
                style = styles["h1"] if item.level <= 1 else styles["h2"]
                story.append(Paragraph(_escape(item.text), style))
            elif item.kind == "paragraph" and item.text:
                story.append(Paragraph(_escape(item.text), styles["body"]))
            elif item.kind == "page_break":
                story.append(PageBreak())
            elif item.kind == "image" and item.image_path and Path(item.image_path).exists():
                with PILImage.open(item.image_path) as source:
                    image_w, image_h = source.size
                scale = min(usable_width / max(image_w, 1), (140 * mm) / max(image_h, 1), 1.0)
                story.append(Spacer(1, 3 * mm))
                story.append(RLImage(item.image_path, width=image_w * scale, height=image_h * scale))
                if item.text:
                    story.append(Paragraph(_escape(item.text), styles["caption"]))
                story.append(Spacer(1, 3 * mm))

    if len(story) <= 2:
        story.append(Paragraph("Kein OCR-Text vorhanden.", styles["body"]))
    document.build(story)
    return dest
