from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from html import escape

from slugify import slugify

from ..models import Block, BookMetadata, Chapter, PageResult
from .text_cleanup import cleanup_german_ocr


HEADING_RE = re.compile(
    r"^\s*(?:(?:kapitel|chapter|teil|section|abschnitt)\s+(?:\d+(?:\.\d+)?|[IVXLCM]{2,})"
    r"|buch\s+\d+"
    r"|[IVXLCM]{2,}\.\s+\S"
    r")",
    re.IGNORECASE,
)
TOC_RE = re.compile(r"inhaltsverzeichnis|table of contents|\binhalt\b", re.IGNORECASE)
HYPHEN_RE = re.compile(r"(\w)-\n(\w)", re.UNICODE)
SOFT_BREAK_RE = re.compile(r"(?<=\w)\n(?=\w)")
NARRATIVE_WORD_RE = re.compile(
    r"\b(war|waren|hatte|hatten|würde|würden|könnte|könnten|wird|wurde|wurden|"
    r"doch|denn|weil|wenn|als|und|oder|aber|schon|noch|mal|nur|auch|sehr|"
    r"ihm|ihr|ihn|sein|seine|seinem|eine|einen|einem|einer|eines|"
    r"der|die|das|den|dem|des|beim|vom|zur|zum|nach|vor|über|unter)\b",
    re.IGNORECASE,
)
OCR_INLINE_HYPHEN_RE = re.compile(r"([a-zäöüß])-\s+([a-zäöüß])", re.IGNORECASE)


INCOMPLETE_ENDING_RE = re.compile(
    r"(?:\b(?:der|die|das|den|dem|des|ein|eine|einen|einem|einer|beim|vom|zur|zum|"
    r"und|oder|mit|von|zu|in|an|auf|für|dass|wenn|weil|doch|denn|war|hatte|wird|"
    r"könnte|würde|noch|schon|mal|nur|auch|sehr|ihm|ihr|ihn|sein|seine|seinem)\s*)$",
    re.IGNORECASE,
)


def repair_hyphenation(text: str, *, preserve_line_breaks: bool = False) -> str:
    text = text.replace("\r\n", "\n")
    text = HYPHEN_RE.sub(r"\1\2", text)
    text = re.sub(r"([.!?])([A-ZÄÖÜ])", r"\1 \2", text)
    paragraphs = []
    for block in re.split(r"\n\s*\n", text):
        if preserve_line_breaks:
            lines = [re.sub(r"[ \t]+", " ", line).strip() for line in block.split("\n")]
            cleaned = "\n".join(line for line in lines if line)
        else:
            cleaned = SOFT_BREAK_RE.sub(" ", block)
            cleaned = re.sub(r"[ \t]+", " ", cleaned).strip()
        if cleaned:
            paragraphs.append(cleaned)
    return "\n\n".join(paragraphs)


_SENTENCE_END_RE = re.compile(r'[.!?…»«"]\s*$')


def _is_duplicate_tail_line(prev: str, curr: str) -> bool:
    """Drop OCR echo lines like «…überbringen konnte.» followed by «ten überbringen konnte.»."""
    prev_cf = prev.casefold().strip()
    curr_cf = curr.casefold().strip()
    if not curr_cf:
        return True
    if prev_cf.endswith(curr_cf):
        return True
    curr_words = curr_cf.split()
    prev_words = prev_cf.split()
    if len(curr_words) >= 2 and prev_words[-len(curr_words) + 1 :] == curr_words[1:]:
        return True
    return False


def _should_join_wrapped_lines(prev: str, curr: str) -> bool:
    """Join text broken across a printed line or page break (e.g. «in ihren» + «Augen war …»)."""
    prev = prev.rstrip()
    curr = curr.lstrip()
    if not prev or not curr:
        return False
    if _SENTENCE_END_RE.search(prev):
        return False
    if prev.endswith("-"):
        return True
    prev_words = prev.split()
    curr_words = curr.split()
    if not prev_words or not curr_words:
        return False
    last, first = prev_words[-1], curr_words[0]
    if last and first and last[-1].isalnum() and last[0].islower() and first[0].isupper():
        return True
    return False


def _coalesce_paragraph_lines(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        if not line:
            continue
        if out and _is_duplicate_tail_line(out[-1], line):
            continue
        if out and _should_join_wrapped_lines(out[-1], line):
            out[-1] = f"{out[-1].rstrip()} {line.lstrip()}"
            continue
        out.append(line)
    return out


def _append_paragraph_item(items: list[FlowItem], para: str) -> None:
    if items and items[-1].kind == "page_break" and len(items) >= 2:
        prev = items[-2]
        if prev.kind == "paragraph" and _should_join_wrapped_lines(prev.text, para):
            prev.text = f"{prev.text.rstrip()} {para.lstrip()}"
            items.pop()
            return
    if items and items[-1].kind == "paragraph":
        last = items[-1]
        if _is_duplicate_tail_line(last.text, para):
            return
        if _should_join_wrapped_lines(last.text, para):
            last.text = f"{last.text.rstrip()} {para.lstrip()}"
            return
    items.append(FlowItem(kind="paragraph", text=para))


def polish_book_line(line: str) -> str:
    """Light cleanup for German OCR (guillemets, stray hyphens, spacing)."""
    line = cleanup_german_ocr(line)
    line = OCR_INLINE_HYPHEN_RE.sub(r"\1\2", line)
    line = re.sub(r"»\s+", "»", line)
    line = re.sub(r"\s+«", "«", line)
    line = re.sub(r"«\s*,", "«,", line)
    line = re.sub(r"\s+,", ",", line)
    line = re.sub(r"\.\s+\.\s+\.", "...", line)
    line = re.sub(r"[ \t]+", " ", line).strip()
    return line


def flow_paragraphs_from_text(text: str) -> list[str]:
    """One EPUB/PDF paragraph per printed book line (dialogue and prose)."""
    text = _ensure_book_line_breaks(text)
    text = repair_hyphenation(text, preserve_line_breaks=True)
    paragraphs: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        for line in block.split("\n"):
            cleaned = polish_book_line(line)
            if cleaned:
                paragraphs.append(cleaned)
    return _coalesce_paragraph_lines(paragraphs)


def _ensure_book_line_breaks(text: str) -> str:
    if "\n" in text:
        return text
    text = re.sub(r"([.!?])\s+(»\s*)", r"\1\n\2", text)
    text = re.sub(r"(»[^»\n]*?«)\s*,?\s+(»\s*)", r"\1\n\2", text)
    return text


def _is_dialogue_line(line: str) -> bool:
    stripped = line.lstrip()
    return stripped.startswith("»") or stripped.startswith("«")


def _title_char_width(text: str, bbox: list[float]) -> float:
    if len(bbox) < 4 or not text:
        return 0.0
    width = max(float(bbox[2]) - float(bbox[0]), 1.0)
    return width / max(len(text.strip()), 1)


def _title_box_height(bbox: list[float]) -> float:
    if len(bbox) < 4:
        return 0.0
    return max(float(bbox[3]) - float(bbox[1]), 0.0)


def _clean_title_line(text: str) -> str:
    return text.strip().split("\n", 1)[0].strip()


def _is_heading(block: Block) -> bool:
    if block.label in {"doc_title", "content_title", "title"}:
        return bool(block.content)
    if not block.content:
        return False
    first = _clean_title_line(block.content)
    if len(first) > 120:
        return False
    if re.match(r"(?i)abschnitt\s+\d+", first) and len(first.split()) > 4:
        return False
    if block.label == "paragraph_title":
        return bool(HEADING_RE.match(first))
    return bool(HEADING_RE.match(first))


def is_section_title(text: str, bbox: list[float], page: PageResult) -> bool:
    """Likely in-book section/chapter title: short, large type, top-centre — not body text."""
    cleaned = _clean_title_line(text)
    if not cleaned or len(cleaned) > 50:
        return False
    if re.search(r"[«»„“\"?]", cleaned):
        return False
    if cleaned[0].islower() or cleaned.startswith(("-", "—", "–")):
        return False
    if cleaned.endswith((".", ",", ";", ":", "«", "»", "-", "—", "–")):
        return False
    if INCOMPLETE_ENDING_RE.search(cleaned):
        return False
    words = cleaned.split()
    if len(words) < 2 or len(words) > 8:
        return False
    if HEADING_RE.match(cleaned):
        if re.match(r"(?i)abschnitt\s+\d+", cleaned) and len(words) > 4:
            return False
        return True
    if re.match(r"^\s*\d+\.\s+", cleaned):
        return False
    if "," in cleaned and len(words) > 4:
        return False
    if "," in cleaned and len(words) > 4:
        return False
    normalized = re.sub(r"[^\w\s-]", " ", cleaned, flags=re.UNICODE)
    if len(words) >= 5 and NARRATIVE_WORD_RE.search(normalized):
        return False
    tiny_words = sum(1 for word in words if len(word) <= 2)
    if tiny_words >= 2 and len(words) >= 4:
        return False
    if not page.height or len(bbox) < 4:
        return False
    if bbox[1] > page.height * 0.28:
        return False
    char_w = _title_char_width(cleaned, bbox)
    if char_w < 26.0:
        return False
    if _title_box_height(bbox) < 68.0:
        return False
    if page.width:
        x_center = (bbox[0] + bbox[2]) / 2.0
        if abs(x_center - page.width / 2.0) > page.width * 0.25:
            return False
    return True


def is_markdown_heading(block: Block, page: PageResult | None = None) -> bool:
    if block.label in {"doc_title", "content_title", "title"}:
        return bool(block.content)
    if block.label == "paragraph_title" and page is not None:
        return is_section_title(block.content, block.bbox, page)
    return _is_heading(block)


def _is_toc(text: str) -> bool:
    return bool(TOC_RE.search(text[:400]))


def _chapter_title_from_block(block: Block, page: PageResult) -> str | None:
    if _is_heading(block):
        return repair_hyphenation(block.content).split("\n", 1)[0][:120]
    if block.label == "paragraph_title" and is_section_title(block.content, block.bbox, page):
        return repair_hyphenation(block.content).split("\n", 1)[0][:120]
    return None


def detect_chapters(pages: list[PageResult], default_title: str = "") -> list[Chapter]:
    headings: list[tuple[int, str]] = []
    for page in pages:
        if page.skipped:
            continue
        page_text = page.markdown or " ".join(b.content for b in page.blocks)
        if _is_toc(page_text):
            continue
        for block in page.blocks[:4]:
            title = _chapter_title_from_block(block, page)
            if title:
                headings.append((page.index, title))
                break
        if any(page.index == p for p, _ in headings):
            continue
        if page.markdown and HEADING_RE.match(page.markdown.strip().split("\n", 1)[0]):
            title = page.markdown.strip().split("\n", 1)[0][:120]
            headings.append((page.index, title.lstrip("# ").strip()))

    if not headings:
        last = pages[-1].index if pages else 0
        title = default_title.strip() or "Buch"
        return [Chapter(id="chapter-1", title=title, start_page=0, end_page=last)]

    unique: list[tuple[int, str]] = []
    for page_index, title in headings:
        if unique and unique[-1][1].casefold() == title.casefold() and page_index - unique[-1][0] <= 1:
            continue
        unique.append((page_index, title))

    first_page = min((p.index for p in pages if not p.skipped), default=0)
    if unique[0][0] > first_page:
        unique.insert(0, (first_page, default_title.strip() or "Anfang"))

    last_page = max(p.index for p in pages)
    chapters: list[Chapter] = []
    for i, (start, title) in enumerate(unique):
        end = unique[i + 1][0] - 1 if i + 1 < len(unique) else last_page
        chapters.append(
            Chapter(
                id=f"chapter-{i + 1}",
                title=title or f"Kapitel {i + 1}",
                start_page=start,
                end_page=max(start, end),
            )
        )
    return _collapse_section_title_splits(pages, chapters, default_title)


def _collapse_section_title_splits(
    pages: list[PageResult],
    chapters: list[Chapter],
    default_title: str,
) -> list[Chapter]:
    """Do not keep a lone section title on page 1 as its own chapter."""
    if len(chapters) != 2:
        return chapters
    first, second = chapters[0], chapters[1]
    if first.start_page != 0 or first.end_page != 0:
        return chapters
    page0 = next((page for page in pages if page.index == 0), None)
    if not page0:
        return chapters
    section_titles = {
        block.content.strip().casefold()
        for block in page0.blocks[:4]
        if block.label == "paragraph_title" and is_section_title(block.content, block.bbox, page0)
    }
    if first.title.strip().casefold() in section_titles or (
        section_titles and first.end_page == 0
    ):
        last = max(page.index for page in pages if not page.skipped)
        title = default_title.strip() or second.title or first.title or "Buch"
        return [Chapter(id="chapter-1", title=title, start_page=0, end_page=last)]
    return chapters


def infer_metadata(pages: list[PageResult], options_title: str, options_author: str, language: str) -> BookMetadata:
    title = options_title.strip()
    if not title:
        for page in pages[:5]:
            for block in page.blocks:
                if block.label == "doc_title" and block.content:
                    title = repair_hyphenation(block.content).split("\n", 1)[0]
                    break
            if title:
                break
    if not title:
        title = "Unbenanntes Buch"
    lang = "de" if language in {"deu", "de", "auto"} else "en"
    return BookMetadata(
        title=title,
        author=options_author.strip(),
        language=lang,
        cover_mode="typographic",
        cover_page=0,
        cover_path=None,
    )


@dataclass
class FlowItem:
    kind: str
    text: str = ""
    image_path: str | None = None
    level: int = 1


def _block_heading_text(block: Block, page: PageResult) -> str | None:
    if _is_heading(block):
        return repair_hyphenation(block.content).split("\n", 1)[0]
    if block.label == "paragraph_title" and is_section_title(block.content, block.bbox, page):
        return repair_hyphenation(block.content).split("\n", 1)[0]
    return None


def _escape_html(text: str) -> str:
    return escape(text)


def chapter_flow(pages: list[PageResult], chapter: Chapter) -> list[FlowItem]:
    items: list[FlowItem] = [FlowItem(kind="heading", text=chapter.title, level=1)]
    page_seen = False
    for page in pages:
        if page.skipped or page.index < chapter.start_page or page.index > chapter.end_page:
            continue
        if page_seen:
            items.append(FlowItem(kind="page_break"))
        page_seen = True
        used_heading = False
        blocks = list(page.blocks)
        if not blocks and page.markdown:
            text = page.markdown
            if not _is_toc(repair_hyphenation(text)):
                for para in flow_paragraphs_from_text(text):
                    _append_paragraph_item(items, para)
            continue
        for block in blocks:
            if not block.content and not block.image_path:
                continue
            heading = _block_heading_text(block, page)
            if heading:
                if page.index == chapter.start_page and not used_heading:
                    used_heading = True
                    continue
                items.append(FlowItem(kind="heading", text=heading, level=2))
                continue
            if block.image_path:
                items.append(
                    FlowItem(
                        kind="image",
                        image_path=block.image_path,
                        text=repair_hyphenation(block.content) if block.content else "",
                    )
                )
                continue
            if _is_toc(block.content):
                continue
            for para in flow_paragraphs_from_text(block.content):
                _append_paragraph_item(items, para)
    return items


def chapter_html(pages: list[PageResult], chapter: Chapter) -> str:
    chunks: list[str] = []
    for item in chapter_flow(pages, chapter):
        if item.kind == "heading":
            tag = "h1" if item.level <= 1 else "h2"
            chunks.append(f"<{tag}>{escape(item.text)}</{tag}>")
        elif item.kind == "image" and item.image_path:
            name = PathRel(item.image_path)
            chunks.append(f'<p class="figure"><img src="{escape(name)}" alt="Abbildung"/></p>')
            if item.text:
                chunks.append(f'<p class="caption"><em>{escape(item.text)}</em></p>')
        elif item.kind == "paragraph" and item.text:
            css = "dialogue" if _is_dialogue_line(item.text) else "prose"
            if css == "dialogue":
                style = "margin:0.15em 0 0.35em 0;text-align:left;"
            else:
                style = "margin:0 0 0.45em 0;text-align:justify;"
            chunks.append(f'<p class="{css}" style="{style}">{_escape_html(item.text)}</p>')
    return "\n".join(chunks)


def PathRel(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    if "." in name:
        stem, ext = name.rsplit(".", 1)
    else:
        stem, ext = name, "jpg"
    slug = slugify(unicodedata.normalize("NFKD", stem), separator="_") or "image"
    return f"images/{slug}.{ext.lower()}"


def merge_chapters(chapters: list[Chapter], left_id: str) -> list[Chapter]:
    result: list[Chapter] = []
    skip_next = False
    for i, chapter in enumerate(chapters):
        if skip_next:
            skip_next = False
            continue
        if chapter.id == left_id and i + 1 < len(chapters):
            nxt = chapters[i + 1]
            result.append(
                Chapter(
                    id=chapter.id,
                    title=chapter.title,
                    start_page=chapter.start_page,
                    end_page=nxt.end_page,
                )
            )
            skip_next = True
            continue
        result.append(chapter)
    return _reindex(result)


def merge_all_chapters(chapters: list[Chapter], title: str = "Buch") -> list[Chapter]:
    if not chapters:
        return chapters
    return [
        Chapter(
            id="chapter-1",
            title=title or chapters[0].title or "Buch",
            start_page=chapters[0].start_page,
            end_page=chapters[-1].end_page,
        )
    ]


def split_chapter(chapters: list[Chapter], chapter_id: str, at_page: int) -> list[Chapter]:
    result: list[Chapter] = []
    for chapter in chapters:
        if chapter.id != chapter_id or not (chapter.start_page < at_page <= chapter.end_page):
            result.append(chapter)
            continue
        result.append(Chapter(id=chapter.id, title=chapter.title, start_page=chapter.start_page, end_page=at_page - 1))
        result.append(
            Chapter(
                id=f"{chapter.id}-b",
                title=f"{chapter.title} (Fortsetzung)",
                start_page=at_page,
                end_page=chapter.end_page,
            )
        )
    return _reindex(result)


def _reindex(chapters: list[Chapter]) -> list[Chapter]:
    out: list[Chapter] = []
    for i, chapter in enumerate(chapters, start=1):
        out.append(Chapter(id=f"chapter-{i}", title=chapter.title, start_page=chapter.start_page, end_page=chapter.end_page))
    return out
