from __future__ import annotations

import shutil
import threading
import traceback
import uuid
from pathlib import Path

from .models import (
    BookMetadata,
    Chapter,
    CreateJobRequest,
    JobProgress,
    JobState,
)
from .paths import job_dir, jobs_root
from .pipeline.checkpoint import (
    apply_ocr_checkpoints,
    batch_indices,
    batch_progress_message,
    can_resume,
    count_completed_ocr,
    load_page_ocr,
    load_preprocess_checkpoint,
    load_run_checkpoint,
    load_source_plan,
    save_preprocess_checkpoint,
    save_run_checkpoint,
)
from .pipeline.dedupe import dedupe_pages
from .pipeline.export_epub import export_epub
from .pipeline.export_pdf import export_pdf
from .pipeline.ingest import ingest
from .pipeline.ocr import ocr_page, release_gpu_after_ocr_batch
from .pipeline.preprocess import preprocess_pages, reorder_spread_pages, sort_pages_by_printed_page
from .pipeline.stream import finalize_pages, process_streaming, uses_streaming
from .pipeline.structure import detect_chapters, infer_metadata, merge_chapters, split_chapter


_lock = threading.Lock()
_jobs: dict[str, JobState] = {}
_cancelled: set[str] = set()
_worker_lock = threading.Lock()
_condition = threading.Condition()


def _is_cancelled(job_id: str) -> bool:
    with _lock:
        return job_id in _cancelled


def _persist(job: JobState) -> None:
    if _is_cancelled(job.id):
        return
    path = job_dir(job.id) / "job.json"
    path.write_text(job.model_dump_json(indent=2), encoding="utf-8")


def get_job(job_id: str) -> JobState:
    if _is_cancelled(job_id):
        raise KeyError(job_id)
    with _lock:
        job = _jobs.get(job_id)
    if job:
        return job
    path = job_dir(job_id) / "job.json"
    if not path.exists():
        raise KeyError(job_id)
    job = JobState.model_validate_json(path.read_text(encoding="utf-8"))
    with _lock:
        _jobs[job_id] = job
    return job


def list_jobs() -> list[JobState]:
    with _lock:
        return list(_jobs.values())


def _job_summary(path: Path, job: JobState) -> dict:
    pages = load_preprocess_checkpoint(path) or job.pages
    completed = count_completed_ocr(path, pages) if pages else job.progress.ocr_completed
    total = len(pages) or job.progress.total_pages
    meta = load_run_checkpoint(path) or {}
    plan = load_source_plan(path)
    message = job.progress.message
    if plan and meta.get("streaming"):
        next_source = int(meta.get("next_source_index", 0))
        total = plan.source_count
        if pages:
            message = f"Quellen {next_source}/{total}, OCR {completed}/{len(pages)} Seite(n)"
    epub = Path(job.epub_path) if job.epub_path else None
    pdf = Path(job.pdf_path) if job.pdf_path else None
    return {
        "id": job.id,
        "title": job.metadata.title or job.options.title or "Unbenanntes Buch",
        "status": job.status,
        "ocr_completed": completed,
        "total_pages": total,
        "message": message,
        "updated_at": int(path.stat().st_mtime),
        "has_epub": bool(epub and epub.exists()),
        "has_pdf": bool(pdf and pdf.exists()),
        "can_resume": can_resume(path),
        "test_page_only": job.options.test_page_only,
    }


def list_library_jobs() -> list[dict]:
    items: list[dict] = []
    root = jobs_root()
    if not root.is_dir():
        return items
    for path in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not path.is_dir():
            continue
        job_json = path / "job.json"
        if not job_json.exists():
            continue
        try:
            job = JobState.model_validate_json(job_json.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if job.options.test_page_only:
            continue
        items.append(_job_summary(path, job))
    return items


def delete_job(job_id: str) -> None:
    with _lock:
        _cancelled.add(job_id)
        _jobs.pop(job_id, None)
    path = jobs_root() / job_id
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    with _condition:
        _condition.notify_all()


def list_resumable_jobs() -> list[dict]:
    items: list[dict] = []
    for entry in list_library_jobs():
        if entry["can_resume"]:
            items.append(
                {
                    "id": entry["id"],
                    "title": entry["title"],
                    "status": entry["status"],
                    "ocr_completed": entry["ocr_completed"],
                    "total_pages": entry["total_pages"],
                    "message": entry["message"],
                }
            )
    return items


def _update(job_id: str, **kwargs) -> JobState:
    if _is_cancelled(job_id):
        raise KeyError(job_id)
    with _lock:
        job = _jobs[job_id]
        data = job.model_dump()
        data.update(kwargs)
        job = JobState.model_validate(data)
        _jobs[job_id] = job
    _persist(job)
    with _condition:
        _condition.notify_all()
    return job


def _set_progress(
    job_id: str,
    phase: str,
    message: str,
    current: int = 0,
    total: int = 0,
    *,
    ocr_completed: int = 0,
    batch_index: int = 0,
    batch_total: int = 0,
) -> None:
    _update(
        job_id,
        status=phase if phase in {"queued", "ingesting", "preprocessing", "ocr", "structuring", "review", "exporting", "done", "error"} else "ocr",
        progress=JobProgress(
            phase=phase,
            current_page=current,
            total_pages=total,
            message=message,
            ocr_completed=ocr_completed,
            batch_index=batch_index,
            batch_total=batch_total,
        ),
    )


def create_job(request: CreateJobRequest) -> JobState:
    job_id = uuid.uuid4().hex[:12]
    job = JobState(id=job_id, sources=request.sources, options=request.options)
    with _lock:
        _jobs.clear()
        _jobs[job_id] = job
    _persist(job)
    thread = threading.Thread(target=_run_job, args=(job_id, False), daemon=True)
    thread.start()
    return job


def resume_job(job_id: str) -> JobState:
    root = job_dir(job_id)
    if not can_resume(root):
        raise RuntimeError("Für diesen Job gibt es keinen unvollständigen OCR-Fortschritt.")
    job = get_job(job_id)
    with _lock:
        _jobs[job_id] = job
    _update(
        job_id,
        status="queued",
        error=None,
        progress=JobProgress(
            phase="queued",
            message="OCR wird fortgesetzt …",
            total_pages=job.progress.total_pages,
            ocr_completed=job.progress.ocr_completed,
        ),
    )
    thread = threading.Thread(target=_run_job, args=(job_id, True), daemon=True)
    thread.start()
    return get_job(job_id)


def wait_for_change(job_id: str, timeout: float = 1.0) -> JobState:
    with _condition:
        _condition.wait(timeout=timeout)
    return get_job(job_id)


def _run_job(job_id: str, resume: bool) -> None:
    with _worker_lock:
        try:
            if _is_cancelled(job_id):
                return
            _process(job_id, resume=resume)
        except KeyError:
            return
        except Exception as exc:
            if _is_cancelled(job_id):
                return
            try:
                job = get_job(job_id)
            except KeyError:
                return
            root = job_dir(job_id)
            pages = load_preprocess_checkpoint(root) or job.pages
            completed = count_completed_ocr(root, pages) if pages else job.progress.ocr_completed
            _update(
                job_id,
                status="error",
                error=str(exc),
                progress=JobProgress(
                    phase="error",
                    message=str(exc),
                    current_page=job.progress.current_page,
                    total_pages=job.progress.total_pages,
                    ocr_completed=completed,
                    batch_index=job.progress.batch_index,
                    batch_total=job.progress.batch_total,
                ),
            )
            traceback.print_exc()


def _process(job_id: str, *, resume: bool = False) -> None:
    if _is_cancelled(job_id):
        return
    job = get_job(job_id)
    root = job_dir(job_id)
    options = job.options

    if uses_streaming(job.sources, options):
        _process_streaming(job_id, resume=resume)
        return

    _process_legacy(job_id, resume=resume)


def _stream_progress(
    job_id: str,
    phase: str,
    message: str,
    current: int,
    total: int,
    ocr_completed: int,
    batch_index: int,
    batch_total: int,
) -> None:
    _set_progress(
        job_id,
        phase,
        message,
        current=current,
        total=total,
        ocr_completed=ocr_completed,
        batch_index=batch_index,
        batch_total=batch_total,
    )


def _process_streaming(job_id: str, *, resume: bool = False) -> None:
    job = get_job(job_id)
    root = job_dir(job_id)
    options = job.options
    batch_size = max(1, options.batch_size)

    if resume:
        pages = load_preprocess_checkpoint(root) or []
        completed = count_completed_ocr(root, pages)
        meta = load_run_checkpoint(root) or {}
        _update(
            job_id,
            progress=JobProgress(
                phase="ocr",
                current_page=completed,
                total_pages=len(pages),
                message=f"Fortsetzen ab Quelle {int(meta.get('next_source_index', 0)) + 1}",
                ocr_completed=completed,
            ),
        )
    else:
        _set_progress(job_id, "ingesting", "Quellenplan wird erstellt …")

    pages = process_streaming(
        root,
        job.sources,
        options,
        lambda phase, message, current, total, ocr_completed, batch_index, batch_total: _stream_progress(
            job_id,
            phase,
            message,
            current,
            total,
            ocr_completed,
            batch_index,
            batch_total,
        ),
        resume=resume,
    )

    pages = finalize_pages(pages)
    deduped = dedupe_pages(pages)
    pages = deduped.pages
    if deduped.skipped:
        _set_progress(
            job_id,
            "structuring",
            f"{deduped.skipped} doppelte Seite(n) entfernt, {len(pages)} eindeutige Seite(n)",
            total=len(pages),
            ocr_completed=len(pages),
        )

    _set_progress(
        job_id,
        "structuring",
        "Kapitel werden erkannt …",
        total=len(pages),
        ocr_completed=len(pages),
    )
    chapters = detect_chapters(pages, default_title=options.title)
    metadata = infer_metadata(pages, options.title, options.author, options.language)
    meta = load_run_checkpoint(root) or {}
    save_run_checkpoint(
        root,
        phase="review",
        total_pages=len(pages),
        ocr_completed=len(pages),
        batch_size=batch_size,
        next_source_index=int(meta.get("source_count") or 0),
        source_count=int(meta.get("source_count") or 0),
        streaming=True,
    )
    _update(
        job_id,
        status="review",
        pages=pages,
        chapters=chapters,
        metadata=metadata,
        progress=JobProgress(
            phase="review",
            current_page=len(pages),
            total_pages=len(pages),
            message="Bereit zur Prüfung",
            ocr_completed=len(pages),
        ),
    )


def _process_legacy(job_id: str, *, resume: bool = False) -> None:
    job = get_job(job_id)
    root = job_dir(job_id)
    options = job.options
    max_pages = 1 if options.test_page_only else None
    batch_size = 0 if options.test_page_only else max(0, options.batch_size)

    if resume:
        pages = load_preprocess_checkpoint(root)
        if not pages:
            raise RuntimeError("Checkpoint mit Seitenliste fehlt.")
        pages, completed = apply_ocr_checkpoints(root, pages)
        total = len(pages)
        batch_index, batch_total = batch_indices(completed + 1, total, batch_size)
        _update(
            job_id,
            pages=pages,
            progress=JobProgress(
                phase="ocr",
                current_page=min(completed + 1, total),
                total_pages=total,
                message=f"OCR fortsetzen ab Seite {completed + 1} von {total}",
                ocr_completed=completed,
                batch_index=batch_index,
                batch_total=batch_total,
            ),
        )
    else:
        def ingest_progress(message: str, current: int, total: int) -> None:
            _set_progress(job_id, "ingesting", message, current=current, total=total)

        _set_progress(job_id, "ingesting", "Seiten werden geladen …")
        ingested = ingest(
            job.sources,
            root / "raw",
            dpi=options.dpi,
            max_pages=max_pages,
            on_progress=ingest_progress,
        )
        raw_pages = ingested.pages
        if ingested.skipped_duplicates:
            _set_progress(
                job_id,
                "ingesting",
                f"{ingested.skipped_duplicates} doppelte Datei(en) übersprungen, {len(raw_pages)} Seite(n) geladen",
                total=len(raw_pages),
            )

        _set_progress(job_id, "preprocessing", "Seiten werden vorbereitet …", total=len(raw_pages))
        pages = preprocess_pages(
            raw_pages,
            root / "pages",
            enable=options.preprocess,
            split_spreads=options.split_spreads and not options.test_page_only,
            skip_blank=options.skip_blank,
        )
        if options.test_page_only:
            pages = pages[:1]
        if options.checkpoint_pages and not options.test_page_only:
            save_preprocess_checkpoint(root, pages)
        completed = 0
        total = len(pages)
        batch_index, batch_total = batch_indices(1, total, batch_size)
        _update(
            job_id,
            pages=pages,
            progress=JobProgress(
                phase="ocr",
                total_pages=total,
                current_page=0,
                message="OCR-Modelle werden geladen, danach Seite 1 …",
                ocr_completed=0,
                batch_index=batch_index,
                batch_total=batch_total,
            ),
        )

    if not resume:
        pages, completed = apply_ocr_checkpoints(root, pages)
    total = len(pages)
    image_dir = root / "images"

    for offset, page in enumerate(pages):
        if load_page_ocr(root, page) is not None and page.blocks:
            continue
        page_num = offset + 1
        batch_index, batch_total = batch_indices(page_num, total, batch_size)
        message = batch_progress_message(page_num, total, batch_size)
        _set_progress(
            job_id,
            "ocr",
            message,
            current=page_num,
            total=total,
            ocr_completed=completed,
            batch_index=batch_index,
            batch_total=batch_total,
        )
        ocr_page(page, job.options, image_dir)
        completed += 1
        pages[offset] = load_page_ocr(root, page) or page
        if options.checkpoint_pages:
            save_run_checkpoint(
                root,
                phase="ocr",
                total_pages=total,
                ocr_completed=completed,
                batch_size=batch_size,
            )
            if release_gpu_after_ocr_batch(completed, batch_size) and completed < total:
                _set_progress(
                    job_id,
                    "ocr",
                    f"Block {batch_index}/{batch_total} gespeichert · {completed}/{total} Seiten OCR · GPU-Speicher freigegeben",
                    current=page_num,
                    total=total,
                    ocr_completed=completed,
                    batch_index=batch_index,
                    batch_total=batch_total,
                )

    pages = reorder_spread_pages(pages)
    pages = sort_pages_by_printed_page(pages)
    deduped = dedupe_pages(pages)
    pages = deduped.pages
    if deduped.skipped:
        _set_progress(
            job_id,
            "structuring",
            f"{deduped.skipped} doppelte Seite(n) entfernt, {len(pages)} eindeutige Seite(n)",
            total=len(pages),
            ocr_completed=len(pages),
        )

    if options.test_page_only:
        page = pages[0]
        _update(
            job_id,
            status="done",
            pages=pages,
            test_markdown=page.markdown,
            test_image=page.image_path,
            progress=JobProgress(phase="done", current_page=1, total_pages=1, message="Testseite fertig", ocr_completed=1),
        )
        return

    _set_progress(
        job_id,
        "structuring",
        "Kapitel werden erkannt …",
        total=len(pages),
        ocr_completed=len(pages),
    )
    chapters = detect_chapters(pages, default_title=options.title)
    metadata = infer_metadata(pages, options.title, options.author, options.language)
    save_run_checkpoint(
        root,
        phase="review",
        total_pages=len(pages),
        ocr_completed=len(pages),
        batch_size=batch_size,
    )
    _update(
        job_id,
        status="review",
        pages=pages,
        chapters=chapters,
        metadata=metadata,
        progress=JobProgress(
            phase="review",
            current_page=len(pages),
            total_pages=len(pages),
            message="Bereit zur Prüfung",
            ocr_completed=len(pages),
        ),
    )


def update_review(job_id: str, chapters: list[Chapter] | None, metadata: BookMetadata | None) -> JobState:
    job = get_job(job_id)
    payload: dict = {}
    if chapters is not None:
        payload["chapters"] = chapters
    if metadata is not None:
        payload["metadata"] = metadata
    payload["status"] = job.status if job.status == "done" else "review"
    return _update(job_id, **payload)


def job_pages(job: JobState) -> list:
    return _load_pages_for_export(job)


def page_preview_path(job_id: str, page_index: int) -> Path:
    job = get_job(job_id)
    for page in job_pages(job):
        if page.index == page_index:
            path = Path(page.image_path)
            if path.exists():
                return path
    raise KeyError(page_index)


def cover_preview_bytes(job_id: str) -> bytes:
    from .pipeline.cover import resolve_cover_jpeg

    job = get_job(job_id)
    root = job_dir(job_id)
    pages = job_pages(job)
    return resolve_cover_jpeg(job.metadata, root, pages)


def upload_job_cover(job_id: str, data: bytes, filename: str | None, content_type: str | None) -> JobState:
    from .uploads import _extension

    if not data:
        raise ValueError("Leere Datei.")
    job = get_job(job_id)
    root = job_dir(job_id)
    ext = _extension(filename, content_type)
    if ext == ".pdf":
        raise ValueError("Bitte ein Bild (JPG/PNG) als Cover hochladen.")
    dest = root / f"cover_custom{ext if ext != '.jpeg' else '.jpg'}"
    dest.write_bytes(data)
    metadata = job.metadata.model_copy(
        update={"cover_mode": "image", "cover_path": str(dest), "cover_page": 0},
    )
    status = job.status if job.status in {"done", "review"} else "review"
    updated = _update(job_id, metadata=metadata, status=status)
    if updated.status == "done":
        return export_job(job_id)
    return updated


def merge_job_chapters(job_id: str, chapter_id: str) -> JobState:
    job = get_job(job_id)
    return _update(job_id, chapters=merge_chapters(job.chapters, chapter_id), status="review")


def merge_all_job_chapters(job_id: str) -> JobState:
    job = get_job(job_id)
    from .pipeline.structure import merge_all_chapters

    title = job.metadata.title or job.options.title or "Buch"
    return _update(job_id, chapters=merge_all_chapters(job.chapters, title), status="review")


def split_job_chapter(job_id: str, chapter_id: str, at_page: int) -> JobState:
    job = get_job(job_id)
    return _update(job_id, chapters=split_chapter(job.chapters, chapter_id, at_page), status="review")


def export_job(job_id: str) -> JobState:
    job = get_job(job_id)
    if job.status not in {"review", "done"}:
        raise RuntimeError("Export erst nach der OCR-Prüfung möglich.")
    _update(
        job_id,
        status="exporting",
        progress=JobProgress(
            phase="exporting",
            message="Export startet …",
            total_pages=len(job.pages),
            ocr_completed=len(job.pages),
        ),
    )
    thread = threading.Thread(target=_export, args=(job_id,), daemon=True)
    thread.start()
    return get_job(job_id)


def _load_pages_for_export(job: JobState) -> list[PageResult]:
    root = job_dir(job.id)
    pages = load_preprocess_checkpoint(root) or job.pages or []
    if not pages:
        return []
    pages, _ = apply_ocr_checkpoints(root, pages)
    return pages


def _reload_job_from_disk(job_id: str) -> JobState:
    path = jobs_root() / job_id / "job.json"
    if not path.is_file():
        raise KeyError(job_id)
    job = JobState.model_validate_json(path.read_text(encoding="utf-8"))
    with _lock:
        if job_id not in _cancelled:
            _jobs[job_id] = job
    return job


def _export(job_id: str) -> None:
    with _worker_lock:
        try:
            job = _reload_job_from_disk(job_id)
            root = job_dir(job_id)
            pages = _load_pages_for_export(job)
            if not pages:
                raise RuntimeError("Keine OCR-Seiten für den Export vorhanden.")
            _set_progress(
                job_id,
                "exporting",
                "Dateien werden geschrieben …",
                total=len(pages),
                ocr_completed=len(pages),
            )
            epub_path = None
            pdf_path = None
            if job.options.export_epub:
                epub_path = str(
                    export_epub(
                        root / f"{_safe_name(job.metadata.title)}.epub",
                        pages,
                        job.chapters,
                        job.metadata,
                        job_root=root,
                    )
                )
            if job.options.export_pdf:
                pdf_path = str(
                    export_pdf(
                        root / f"{_safe_name(job.metadata.title)}.pdf",
                        pages,
                        job.metadata,
                        job.chapters,
                    )
                )
            _update(
                job_id,
                status="done",
                epub_path=epub_path,
                pdf_path=pdf_path,
                progress=JobProgress(
                    phase="done",
                    current_page=len(pages),
                    total_pages=len(pages),
                    message="Export fertig",
                    ocr_completed=len(pages),
                ),
            )
        except Exception as exc:
            _update(
                job_id,
                status="error",
                error=str(exc),
                progress=JobProgress(phase="error", message=str(exc)),
            )
            traceback.print_exc()


def _safe_name(title: str) -> str:
    from slugify import slugify

    return slugify(title or "buch") or "buch"


def dump_job(job_id: str) -> dict:
    return get_job(job_id).model_dump()
