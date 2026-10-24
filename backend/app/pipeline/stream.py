from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from pathlib import Path

from ..models import JobOptions, PageResult, SourcePlan
from .checkpoint import (
    apply_ocr_checkpoints,
    batch_sources_ocr_complete,
    batch_sources_preprocessed,
    cleanup_raw_sources,
    count_completed_ocr,
    load_or_create_source_plan,
    load_page_ocr,
    load_preprocess_checkpoint,
    load_run_checkpoint,
    merge_preprocess_checkpoint,
    pages_for_source_batch,
    save_preprocess_checkpoint,
    save_run_checkpoint,
    save_source_plan,
    source_batch_indices,
    source_batch_progress_message,
)
from .ingest import ingest_plan_entries
from .ocr import clear_ocr_cache, ocr_page
from .preprocess import preprocess_pages, reorder_spread_pages, sort_pages_by_printed_page
from .source_plan import build_source_plan, should_stream


ProgressFn = Callable[[str, str, int, int, int, int, int], None]


class _BlockIncomplete(Exception):
    """OCR of a block stopped before every page was saved."""


def run_block_queue(blocks: list, prepare: Callable, consume: Callable) -> None:
    """Prepare the next block on a side thread while the current block is consumed."""
    pending: queue.Queue = queue.Queue(maxsize=1)
    stop = threading.Event()
    errors: list[BaseException] = []

    def produce() -> None:
        try:
            for block in blocks:
                if stop.is_set():
                    return
                prepared = prepare(block)
                while not stop.is_set():
                    try:
                        pending.put(prepared, timeout=0.2)
                        break
                    except queue.Full:
                        continue
            while not stop.is_set():
                try:
                    pending.put(None, timeout=0.2)
                    break
                except queue.Full:
                    continue
        except BaseException as exc:
            errors.append(exc)
            pending.put(None)

    worker = threading.Thread(target=produce, name="digitize-preprocess", daemon=True)
    worker.start()
    try:
        while True:
            item = pending.get()
            if item is None:
                break
            consume(item)
    finally:
        stop.set()
        worker.join(timeout=5)
    if errors:
        raise errors[0]


def uses_streaming(sources: list[str], options: JobOptions) -> bool:
    if options.test_page_only or not options.checkpoint_pages:
        return False
    plan = build_source_plan(sources, max_pages=1 if options.test_page_only else None)
    return should_stream(plan, test_page_only=options.test_page_only, checkpoint_pages=options.checkpoint_pages)


def _next_page_index(pages: list[PageResult]) -> int:
    if not pages:
        return 0
    return max(page.index for page in pages) + 1


def process_streaming(
    root: Path,
    sources: list[str],
    options: JobOptions,
    on_progress: ProgressFn,
    *,
    resume: bool = False,
) -> list[PageResult]:
    max_pages = 1 if options.test_page_only else None
    batch_size = max(1, options.batch_size)
    plan = load_or_create_source_plan(root, sources, max_pages=max_pages) if resume else build_source_plan(sources, max_pages=max_pages)
    if not resume:
        save_source_plan(root, plan)

    meta = load_run_checkpoint(root) or {}
    next_source = int(meta.get("next_source_index", 0)) if resume else 0
    pages = load_preprocess_checkpoint(root) or []
    pages, ocr_completed = apply_ocr_checkpoints(root, pages)
    source_count = plan.source_count
    source_batch_total = max(1, (source_count + batch_size - 1) // batch_size)
    image_dir = root / "images"

    save_run_checkpoint(
        root,
        phase="ingesting" if next_source < source_count else "ocr",
        total_pages=len(pages),
        ocr_completed=ocr_completed,
        batch_size=batch_size,
        next_source_index=next_source,
        source_count=source_count,
        streaming=True,
    )

    if plan.skipped_duplicates:
        on_progress(
            "ingesting",
            f"{plan.skipped_duplicates} doppelte Datei(en) übersprungen · {source_count} Seite(n) geplant",
            0,
            source_count,
            ocr_completed,
            0,
            source_batch_total,
        )

    state_lock = threading.Lock()
    progress_lock = threading.Lock()
    state = {"pages": pages, "ocr_completed": ocr_completed, "next_source": next_source, "preparing": 0}

    def report(phase: str, message: str, current: int, total: int, done: int, batch_index: int, batch_total: int) -> None:
        with progress_lock:
            on_progress(phase, message, current, total, done, batch_index, batch_total)

    blocks = list(range(next_source, source_count, batch_size))

    def prepare(block_start: int) -> int:
        batch_end = min(block_start + batch_size, source_count)
        batch_entries = plan.entries[block_start:batch_end]
        source_indices = {entry.source_index for entry in batch_entries}
        batch_index, _ = source_batch_indices(block_start, source_count, batch_size)
        with state_lock:
            state["preparing"] = batch_index
            pages_now = list(state["pages"])
            done = state["ocr_completed"]
            ready = batch_sources_preprocessed(pages_now, source_indices)
        if not ready:
            def ingest_progress(message: str, current: int, total: int) -> None:
                report("ingesting", message, current, total, done, batch_index, source_batch_total)

            report(
                "ingesting",
                source_batch_progress_message(batch_index, source_batch_total, block_start, batch_end, source_count),
                block_start,
                source_count,
                done,
                batch_index,
                source_batch_total,
            )
            raw_pages = ingest_plan_entries(batch_entries, root / "raw", options.dpi, on_progress=ingest_progress)
            report(
                "preprocessing",
                f"Vorbereitung Block {batch_index}/{source_batch_total}: {len(raw_pages)} Seite(n)",
                batch_end,
                source_count,
                done,
                batch_index,
                source_batch_total,
            )
            start_index = _next_page_index(pages_now)
            new_pages = preprocess_pages(
                raw_pages,
                root / "pages",
                enable=options.preprocess,
                split_spreads=options.split_spreads,
                skip_blank=options.skip_blank,
                start_index=start_index,
            )
            merged = merge_preprocess_checkpoint(root, new_pages)
            cleanup_raw_sources(root, source_indices)
            with state_lock:
                state["pages"] = merged
        with state_lock:
            state["preparing"] = 0
        return block_start

    def consume(block_start: int) -> None:
        batch_end = min(block_start + batch_size, source_count)
        source_indices = {entry.source_index for entry in plan.entries[block_start:batch_end]}
        batch_index, _ = source_batch_indices(block_start, source_count, batch_size)
        with state_lock:
            pages_now = list(state["pages"])
            done = state["ocr_completed"]
            preparing = state["preparing"]
        batch_pages = pages_for_source_batch(pages_now, source_indices)
        total_pages = len(pages_now)
        prep_note = f" · Vorbereitung Block {preparing}/{source_batch_total}" if preparing else ""
        for offset, page in enumerate(batch_pages):
            if load_page_ocr(root, page) is not None:
                continue
            report(
                "ocr",
                f"OCR Block {batch_index}/{source_batch_total} · Seite {offset + 1}/{len(batch_pages)}{prep_note}",
                page.index + 1,
                total_pages,
                done,
                batch_index,
                source_batch_total,
            )
            ocr_page(page, options, image_dir)
            done += 1
            loaded = load_page_ocr(root, page)
            with state_lock:
                current_pages = state["pages"]
                if loaded:
                    for index, existing in enumerate(current_pages):
                        if existing.index == page.index:
                            current_pages[index] = loaded
                            break
                state["ocr_completed"] = done
                state["pages"] = current_pages
            save_run_checkpoint(
                root,
                phase="ocr",
                total_pages=total_pages,
                ocr_completed=done,
                batch_size=batch_size,
                next_source_index=block_start,
                source_count=source_count,
                streaming=True,
            )
        with state_lock:
            pages_now = state["pages"]
            done = state["ocr_completed"]
        if not batch_sources_ocr_complete(root, pages_now, source_indices):
            raise _BlockIncomplete()
        clear_ocr_cache(reason=f"streaming_block_{batch_index}")
        with state_lock:
            state["next_source"] = batch_end
        save_run_checkpoint(
            root,
            phase="ocr",
            total_pages=len(pages_now),
            ocr_completed=done,
            batch_size=batch_size,
            next_source_index=batch_end,
            source_count=source_count,
            streaming=True,
        )
        report(
            "ocr",
            f"Block {batch_index}/{source_batch_total} gespeichert · {done} Seite(n) OCR · GPU-Speicher freigegeben",
            len(pages_now),
            len(pages_now),
            done,
            batch_index,
            source_batch_total,
        )

    try:
        run_block_queue(blocks, prepare, consume)
    except _BlockIncomplete:
        pass

    with state_lock:
        pages = state["pages"]
    pages, _ = apply_ocr_checkpoints(root, pages)
    save_preprocess_checkpoint(root, pages)

    meta = load_run_checkpoint(root) or {}
    next_source = int(meta.get("next_source_index", 0))
    if next_source < source_count:
        completed = count_completed_ocr(root, pages)
        raise RuntimeError(
            f"Verarbeitung pausiert bei Quelle {next_source + 1}/{source_count} "
            f"({completed}/{len(pages)} Seiten OCR). Bitte „OCR fortsetzen“ wählen."
        )
    return pages


def finalize_pages(pages: list[PageResult]) -> list[PageResult]:
    pages = reorder_spread_pages(pages)
    pages = sort_pages_by_printed_page(pages)
    return pages
