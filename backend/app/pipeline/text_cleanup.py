from __future__ import annotations

import re

MERGED_WORD_RE = re.compile(r"(?<=[a-zäöüß])(?=[A-ZÄÖÜ])")
PUNCT_SPACE_RE = re.compile(r"([,.!?;:])(?=\S)")
QUOTE_SPACE_RE = re.compile(r"(?<=[»«])(?=\S)")
SPACED_GUILLEMET_OPEN_RE = re.compile(r"<\s+<")
SPACED_GUILLEMET_CLOSE_RE = re.compile(r">\s+>")
MULTISPACE_RE = re.compile(r"[ \t]+")
LATEX_RANGLE_RE = re.compile(r"\$\s*\\rangle\s*\$", re.IGNORECASE)
LATEX_LANGLE_RE = re.compile(r"\$\s*\\langle\s*\$", re.IGNORECASE)
PLAIN_RANGLE_RE = re.compile(r"\\rangle\b", re.IGNORECASE)
PLAIN_LANGLE_RE = re.compile(r"\\langle\b", re.IGNORECASE)
ANGLE_BRACKET_QUOTE_RE = re.compile(r">(\s*[A-Za-zÄÖÜäöüß]\s*)<")


def cleanup_german_ocr(text: str) -> str:
    text = text.replace("\r\n", "\n").strip()
    if not text:
        return ""
    text = LATEX_RANGLE_RE.sub("»", text)
    text = LATEX_LANGLE_RE.sub("«", text)
    text = PLAIN_RANGLE_RE.sub("»", text)
    text = PLAIN_LANGLE_RE.sub("«", text)
    text = ANGLE_BRACKET_QUOTE_RE.sub(lambda m: f"»{m.group(1).strip()}«", text)
    text = text.replace("<<", "«").replace(">>", "»")
    text = SPACED_GUILLEMET_OPEN_RE.sub("«", text)
    text = SPACED_GUILLEMET_CLOSE_RE.sub("»", text)
    text = PUNCT_SPACE_RE.sub(r"\1 ", text)
    text = QUOTE_SPACE_RE.sub(" ", text)
    text = MERGED_WORD_RE.sub(" ", text)
    text = MULTISPACE_RE.sub(" ", text)
    text = re.sub(r"»\s+", "»", text)
    text = re.sub(r"\s+«", "«", text)
    return text.strip()
