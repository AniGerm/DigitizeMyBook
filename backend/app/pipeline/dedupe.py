from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ..models import PageResult


@dataclass
class DedupeStats:
    pages: list[PageResult]
    skipped: int = 0


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def image_file_hash(path: Path) -> str:
    try:
        return content_hash(path.read_bytes())
    except OSError:
        return ""


def dedupe_pages(pages: list[PageResult]) -> DedupeStats:
    """Drop identical scans and repeated printed page numbers within one job."""
    seen_hashes: set[str] = set()
    seen_printed: set[int] = set()
    kept: list[PageResult] = []
    skipped = 0

    for page in pages:
        if page.skipped:
            kept.append(page.model_copy(update={"index": len(kept)}))
            continue

        digest = image_file_hash(Path(page.image_path))
        if digest and digest in seen_hashes:
            skipped += 1
            continue
        if page.printed_page is not None and page.printed_page in seen_printed:
            skipped += 1
            continue

        if digest:
            seen_hashes.add(digest)
        if page.printed_page is not None:
            seen_printed.add(page.printed_page)
        kept.append(page.model_copy(update={"index": len(kept)}))

    return DedupeStats(pages=kept, skipped=skipped)


def dedupe_upload_bytes(files: list[tuple[str | None, bytes, str | None]]) -> tuple[list[tuple[str | None, bytes, str | None]], int]:
    seen: set[str] = set()
    unique: list[tuple[str | None, bytes, str | None]] = []
    skipped = 0
    for item in files:
        _name, data, _ctype = item
        if not data:
            continue
        digest = content_hash(data)
        if digest in seen:
            skipped += 1
            continue
        seen.add(digest)
        unique.append(item)
    return unique, skipped
