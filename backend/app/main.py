from __future__ import annotations

import json
import socket
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import jobs, updates
from .models import BookMetadata, Chapter, CreateJobRequest
from .picker import pick_path
from .pipeline.ocr import gpu_status
from .uploads import store_uploads


app = FastAPI(title="digitizeMyBook", version=updates.app_version())
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[0-1])\.\d+\.\d+)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ReviewUpdate(BaseModel):
    chapters: list[Chapter] | None = None
    metadata: BookMetadata | None = None


class SplitRequest(BaseModel):
    chapter_id: str
    at_page: int


class MergeRequest(BaseModel):
    chapter_id: str


class PickRequest(BaseModel):
    directories: bool = True


@app.on_event("startup")
def _check_updates_on_startup() -> None:
    import threading

    threading.Thread(target=updates.check_for_update, daemon=True).start()


@app.get("/api/health")
def health():
    return {"ok": True, "version": updates.app_version(), "lan_urls": _lan_urls(), **gpu_status()}


@app.get("/api/updates")
def update_status():
    return updates.get_status()


@app.post("/api/updates/check")
def update_check():
    return updates.check_for_update()


@app.post("/api/updates/download")
def update_download():
    return updates.download_update()


@app.post("/api/updates/install")
def update_install():
    return updates.install_update()


@app.post("/api/uploads")
async def upload(files: list[UploadFile] = File(...)):
    payload: list[tuple[str | None, bytes, str | None]] = []
    for item in files:
        payload.append((item.filename, await item.read(), item.content_type))
    try:
        folder, skipped = store_uploads(payload)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "paths": [str(folder)],
        "count": sum(1 for _ in folder.iterdir()),
        "skipped_duplicates": skipped,
    }


@app.post("/api/pick")
def pick(req: PickRequest):
    path = pick_path(directories=req.directories)
    if not path:
        return {"paths": []}
    paths = [p for p in path.split("\n") if p]
    return {"paths": paths}


@app.get("/api/browse")
def browse(path: str):
    folder = Path(path).expanduser()
    if not folder.exists() or not folder.is_dir():
        raise HTTPException(404, "Ordner nicht gefunden")
    entries = []
    for child in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
        if child.name.startswith("."):
            continue
        entries.append({"name": child.name, "path": str(child), "is_dir": child.is_dir()})
    return {"path": str(folder), "entries": entries}


@app.post("/api/jobs")
def create_job(req: CreateJobRequest):
    job = jobs.create_job(req)
    return job.public_dict()


@app.get("/api/jobs")
def list_jobs_library():
    return jobs.list_library_jobs()


@app.get("/api/jobs/resumable")
def list_resumable_jobs():
    return jobs.list_resumable_jobs()


@app.post("/api/jobs/{job_id}/resume")
def resume_job(job_id: str):
    try:
        return jobs.resume_job(job_id).public_dict()
    except KeyError:
        raise HTTPException(404, "Job nicht gefunden")
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    try:
        jobs.delete_job(job_id)
    except KeyError:
        raise HTTPException(404, "Job nicht gefunden")
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    try:
        return jobs.get_job(job_id).public_dict()
    except KeyError:
        raise HTTPException(404, "Job nicht gefunden")


@app.get("/api/jobs/{job_id}/full")
def get_job_full(job_id: str):
    try:
        return jobs.get_job(job_id).model_dump()
    except KeyError:
        raise HTTPException(404, "Job nicht gefunden")


@app.get("/api/jobs/{job_id}/events")
def job_events(job_id: str):
    def stream():
        last = None
        while True:
            try:
                job = jobs.get_job(job_id)
            except KeyError:
                yield f"data: {json.dumps({'error': 'missing'})}\n\n"
                return
            payload = job.public_dict()
            encoded = json.dumps(payload, ensure_ascii=False)
            if encoded != last:
                yield f"data: {encoded}\n\n"
                last = encoded
            if job.status in {"done", "error", "review"}:
                return
            jobs.wait_for_change(job_id, timeout=0.8)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.patch("/api/jobs/{job_id}/review")
def review(job_id: str, update: ReviewUpdate):
    try:
        job = jobs.update_review(job_id, update.chapters, update.metadata)
    except KeyError:
        raise HTTPException(404, "Job nicht gefunden")
    return job.public_dict()


@app.post("/api/jobs/{job_id}/merge")
def merge(job_id: str, req: MergeRequest):
    try:
        return jobs.merge_job_chapters(job_id, req.chapter_id).public_dict()
    except KeyError:
        raise HTTPException(404, "Job nicht gefunden")


@app.post("/api/jobs/{job_id}/merge-all")
def merge_all(job_id: str):
    try:
        return jobs.merge_all_job_chapters(job_id).public_dict()
    except KeyError:
        raise HTTPException(404, "Job nicht gefunden")


@app.post("/api/jobs/{job_id}/split")
def split(job_id: str, req: SplitRequest):
    try:
        return jobs.split_job_chapter(job_id, req.chapter_id, req.at_page).public_dict()
    except KeyError:
        raise HTTPException(404, "Job nicht gefunden")


@app.post("/api/jobs/{job_id}/export")
def export(job_id: str):
    try:
        return jobs.export_job(job_id).public_dict()
    except KeyError:
        raise HTTPException(404, "Job nicht gefunden")
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/jobs/{job_id}/preview/{page_index}")
def preview(job_id: str, page_index: int):
    try:
        path = jobs.page_preview_path(job_id, page_index)
    except KeyError:
        raise HTTPException(404, "Seite nicht gefunden")
    return FileResponse(path)


@app.get("/api/jobs/{job_id}/cover")
def cover_preview(job_id: str):
    try:
        data = jobs.cover_preview_bytes(job_id)
    except KeyError:
        raise HTTPException(404, "Job nicht gefunden")
    return Response(content=data, media_type="image/jpeg")


@app.post("/api/jobs/{job_id}/cover")
async def cover_upload(job_id: str, file: UploadFile = File(...)):
    try:
        data = await file.read()
        job = jobs.upload_job_cover(job_id, data, file.filename, file.content_type)
    except KeyError:
        raise HTTPException(404, "Job nicht gefunden")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return job.public_dict()


@app.get("/api/jobs/{job_id}/download/{kind}")
def download(job_id: str, kind: str):
    try:
        job = jobs.get_job(job_id)
    except KeyError:
        raise HTTPException(404, "Job nicht gefunden")
    path = Path(job.epub_path or "") if kind == "epub" else Path(job.pdf_path or "")
    if not path.exists():
        raise HTTPException(404, "Datei noch nicht erzeugt")
    media = "application/epub+zip" if kind == "epub" else "application/pdf"
    return FileResponse(path, media_type=media, filename=path.name)


def _lan_urls(port: int = 8765) -> list[str]:
    ips: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("1.1.1.1", 80))
            ips.append(sock.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    return [f"http://{ip}:{port}" for ip in ips if ip and not ip.startswith("127.")]


def _maybe_mount_frontend() -> None:
    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")


_maybe_mount_frontend()
