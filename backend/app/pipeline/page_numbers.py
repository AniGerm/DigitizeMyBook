from __future__ import annotations

import re

from ..models import PageResult

_OCR_VON = re.compile(r"\b(?:cln|v0n|uon|vom)\b", re.IGNORECASE)

_PAGE_OF_TOTAL = re.compile(
    r"^[\s\-–—]*(\d{1,4})\s*[/\\|]\s*(\d{1,4})\s*$",
    re.IGNORECASE,
)
_PAGE_WITH_TOTAL = re.compile(
    r"^[\s\-–—]*"
    r"(?:seite|blatt|bl\.?|s\.?|page|p\.?|pg\.?)\s*"
    r"(\d{1,4})\s*"
    r"(?:von|vom|/|\\|of)\s*"
    r"(\d{1,4})\s*$",
    re.IGNORECASE,
)
_COUNT_ONLY = re.compile(
    r"^[\s\-–—]*(\d{1,4})\s*(?:von|vom|/|\\|of)\s*(\d{1,4})\s*$",
    re.IGNORECASE,
)
_PREFIX_NUMBER = re.compile(
    r"^[\s\-–—]*(?:seite|blatt|bl\.?|s\.?|page|p\.?|pg\.?)\s*(\d{1,4})\s*$",
    re.IGNORECASE,
)
_NUMBER_ONLY = re.compile(r"^[\s\-–—]*(\d{1,4})[\s\-–—]*$")


def normalize_page_hint(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text.strip())
    cleaned = cleaned.replace("|", "/")
    cleaned = _OCR_VON.sub("von", cleaned)
    return cleaned


def parse_printed_page_number(text: str) -> int | None:
    """Extract a page number from common German/English footer/header formats."""
    normalized = normalize_page_hint(text)
    if not normalized:
        return None

    for pattern in (_PAGE_WITH_TOTAL, _COUNT_ONLY, _PAGE_OF_TOTAL, _PREFIX_NUMBER, _NUMBER_ONLY):
        match = pattern.match(normalized)
        if not match:
            continue
        value = int(match.group(1))
        if 1 <= value <= 9999:
            return value
    return None


def page_hint_priority(text: str, bbox: list[float], page: PageResult) -> int:
    """Higher score = more likely to be the real printed page marker."""
    normalized = normalize_page_hint(text)
    score = 0
    if _PAGE_WITH_TOTAL.match(normalized) or _COUNT_ONLY.match(normalized):
        score += 100
    elif _PREFIX_NUMBER.match(normalized):
        score += 80
    elif _PAGE_OF_TOTAL.match(normalized):
        score += 70
    elif _NUMBER_ONLY.match(normalized):
        score += 40

    if page.height and len(bbox) >= 4:
        y_center = (bbox[1] + bbox[3]) / 2.0
        if y_center >= page.height * 0.84:
            score += 25
        elif y_center <= page.height * 0.16:
            score += 15
        if page.width and len(bbox) >= 4:
            x_center = (bbox[0] + bbox[2]) / 2.0
            if abs(x_center - page.width / 2.0) <= page.width * 0.22:
                score += 10
    return score


def is_margin_page_hint(bbox: list[float], page: PageResult, text: str) -> bool:
    """Page hints must sit in a header/footer band; plain numbers stay strict."""
    if not page.height or len(bbox) < 4:
        return parse_printed_page_number(text) is not None

    y_center = (bbox[1] + bbox[3]) / 2.0
    in_margin = y_center <= page.height * 0.16 or y_center >= page.height * 0.84
    normalized = normalize_page_hint(text)
    if _NUMBER_ONLY.match(normalized):
        return in_margin
    if in_margin:
        return True
    # Allow explicit "Seite X von Y" labels slightly deeper in the footer band.
    if y_center >= page.height * 0.78 and (
        _PAGE_WITH_TOTAL.match(normalized)
        or _COUNT_ONLY.match(normalized)
        or _PREFIX_NUMBER.match(normalized)
    ):
        return True
    return False


def pick_printed_page(candidates: list[tuple[str, list[float], PageResult]]) -> int | None:
    best: tuple[int, int] | None = None
    for text, bbox, page in candidates:
        value = parse_printed_page_number(text)
        if value is None or not is_margin_page_hint(bbox, page, text):
            continue
        priority = page_hint_priority(text, bbox, page)
        if best is None or priority > best[0]:
            best = (priority, value)
    return best[1] if best else None
