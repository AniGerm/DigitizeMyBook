from __future__ import annotations

import json
import threading
from pathlib import Path

from ..models import PageResult, SourcePlan
from .source_plan import build_source_plan


_io_lock = threading.RLock()


def checkpoint_dir(root: Path) -> Path:
    path = root / "checkpoint"
    path.mkdir(parents=True, exist_ok=True)
    return path


def ocr_path(root: Path, page_index: int) -> Path:
    return root / "ocr" / f"{page_index:04d}.json"


def save_preprocess_checkpoint(root: Path, pages: list[PageResult]) -> None:
    dest = checkpoint_dir(root) / "pages.json"
    payload = json.dumps([page.model_dump() for page in pages], ensure_ascii=False, indent=2)
    with _io_lock:
        dest.write_text(payload, encoding="utf-8")


def load_preprocess_checkpoint(root: Path) -> list[PageResult] | None:
    path = checkpoint_dir(root) / "pages.json"
    with _io_lock:
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        return None
    return [PageResult.model_validate(item) for item in data]


def merge_preprocess_checkpoint(root: Path, new_pages: list[PageResult]) -> list[PageResult]:
    existing = load_preprocess_checkpoint(root) or []
    by_index = {page.index: page for page in existing}
    for page in new_pages:
        by_index[page.index] = page
    merged = [by_index[index] for index in sorted(by_index)]
    save_preprocess_checkpoint(root, merged)
    return merged


def save_source_plan(root: Path, plan: SourcePlan) -> None:
    (checkpoint_dir(root) / "source_plan.json").write_text(
        plan.model_dump_json(indent=2),
        encoding="utf-8",
    )


def load_source_plan(root: Path) -> SourcePlan | None:
    path = checkpoint_dir(root) / "source_plan.json"
    if not path.exists():
        return None
    try:
        return SourcePlan.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def load_or_create_source_plan(root: Path, sources: list[str], max_pages: int | None = None) -> SourcePlan:
    plan = load_source_plan(root)
    if plan:
        return plan
    plan = build_source_plan(sources, max_pages=max_pages)
    save_source_plan(root, plan)
    return plan


def load_page_ocr(root: Path, page: PageResult) -> PageResult | None:
    path = ocr_path(root, page.index)
    if not path.exists():
        return None
    try:
        loaded = PageResult.model_validate_json(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        return None
    if loaded.image_path != page.image_path:
        return None
    if loaded.skipped and not loaded.markdown and not loaded.blocks:
        return loaded
    if not loaded.skipped and not loaded.markdown and not loaded.blocks:
        return None
    return loaded


def apply_ocr_checkpoints(root: Path, pages: list[PageResult]) -> tuple[list[PageResult], int]:
    out: list[PageResult] = []
    completed = 0
    for page in pages:
        loaded = load_page_ocr(root, page)
        if loaded is not None:
            out.append(loaded)
            completed += 1
        else:
            out.append(page)
    return out, completed


def load_all_pages_with_ocr(root: Path) -> list[PageResult]:
    pages = load_preprocess_checkpoint(root) or []
    return apply_ocr_checkpoints(root, pages)[0]


def save_run_checkpoint(
    root: Path,
    *,
    phase: str,
    total_pages: int,
    ocr_completed: int,
    batch_size: int,
    next_source_index: int | None = None,
    source_count: int | None = None,
    streaming: bool | None = None,
) -> None:
    meta = load_run_checkpoint(root) or {}
    meta.update(
        {
            "phase": phase,
            "total_pages": total_pages,
            "ocr_completed": ocr_completed,
            "batch_size": batch_size,
        }
    )
    if next_source_index is not None:
        meta["next_source_index"] = next_source_index
    if source_count is not None:
        meta["source_count"] = source_count
    if streaming is not None:
        meta["streaming"] = streaming
    payload = json.dumps(meta, ensure_ascii=False, indent=2)
    with _io_lock:
        (checkpoint_dir(root) / "run.json").write_text(payload, encoding="utf-8")


def load_run_checkpoint(root: Path) -> dict | None:
    path = checkpoint_dir(root) / "run.json"
    with _io_lock:
        if not path.exists():
            return None
        raw = path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def batch_progress_message(page_num: int, total: int, batch_size: int) -> str:
    if batch_size <= 0 or total <= 0:
        return f"OCR Seite {page_num} von {total}"
    batch_index = (page_num - 1) // batch_size + 1
    batch_total = max(1, (total + batch_size - 1) // batch_size)
    batch_start = (batch_index - 1) * batch_size + 1
    batch_end = min(batch_index * batch_size, total)
    return (
        f"Block {batch_index}/{batch_total} · OCR Seite {page_num} von {total} "
        f"(Seiten {batch_start}–{batch_end})"
    )


def source_batch_progress_message(
    batch_index: int,
    batch_total: int,
    source_start: int,
    source_end: int,
    source_count: int,
) -> str:
    return (
        f"Block {batch_index}/{batch_total} · Quellen {source_start + 1}–{source_end} "
        f"von {source_count}"
    )


def batch_indices(page_num: int, total: int, batch_size: int) -> tuple[int, int]:
    if batch_size <= 0 or total <= 0:
        return 0, 0
    batch_index = (page_num - 1) // batch_size + 1
    batch_total = max(1, (total + batch_size - 1) // batch_size)
    return batch_index, batch_total


def source_batch_indices(source_start: int, source_count: int, batch_size: int) -> tuple[int, int]:
    if batch_size <= 0 or source_count <= 0:
        return 0, 0
    batch_index = source_start // batch_size + 1
    batch_total = max(1, (source_count + batch_size - 1) // batch_size)
    return batch_index, batch_total


def count_completed_ocr(root: Path, pages: list[PageResult]) -> int:
    return sum(1 for page in pages if load_page_ocr(root, page) is not None)


def pages_for_source_batch(pages: list[PageResult], source_indices: set[int]) -> list[PageResult]:
    selected = [
        page
        for page in pages
        if page.source_index in source_indices or (page.source_index is None and page.spread_id in source_indices)
    ]
    return sorted(selected, key=lambda page: page.index)


def batch_sources_preprocessed(pages: list[PageResult], source_indices: set[int]) -> bool:
    if not source_indices:
        return True
    covered = {
        page.source_index
        for page in pages
        if page.source_index is not None and page.source_index in source_indices
    }
    covered.update(
        page.spread_id
        for page in pages
        if page.spread_id is not None and page.spread_id in source_indices and page.source_index is None
    )
    return source_indices <= covered


def batch_sources_ocr_complete(root: Path, pages: list[PageResult], source_indices: set[int]) -> bool:
    batch_pages = pages_for_source_batch(pages, source_indices)
    if not batch_pages:
        return False
    return all(load_page_ocr(root, page) is not None for page in batch_pages)


def cleanup_raw_sources(root: Path, source_indices: set[int]) -> None:
    raw_dir = root / "raw"
    if not raw_dir.is_dir():
        return
    for source_index in source_indices:
        path = raw_dir / f"{source_index:04d}.png"
        path.unlink(missing_ok=True)


def can_resume(root: Path) -> bool:
    meta = load_run_checkpoint(root) or {}
    plan = load_source_plan(root)
    pages = load_preprocess_checkpoint(root) or []

    if meta.get("streaming") and plan:
        next_source = int(meta.get("next_source_index", 0))
        if next_source < plan.source_count:
            return next_source > 0 or bool(pages)
        if pages:
            completed = count_completed_ocr(root, pages)
            return 0 < completed < len(pages)
        return False

    if not pages:
        return False
    total = int(meta.get("total_pages") or len(pages))
    completed = count_completed_ocr(root, pages)
    return 0 < completed < total
