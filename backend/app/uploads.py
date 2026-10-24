from __future__ import annotations

import uuid
from pathlib import Path

from .pipeline.dedupe import dedupe_upload_bytes
from .paths import uploads_root

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp", ".heic", ".heif"}
ALLOWED_EXTS = IMAGE_EXTS | {".pdf"}


def _extension(filename: str | None, content_type: str | None) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix in ALLOWED_EXTS:
        return ".jpg" if suffix == ".jpeg" else suffix
    ctype = (content_type or "").lower()
    if "pdf" in ctype:
        return ".pdf"
    if "png" in ctype:
        return ".png"
    if "webp" in ctype:
        return ".webp"
    if "heic" in ctype or "heif" in ctype:
        return ".heic"
    if "tif" in ctype:
        return ".tif"
    return ".jpg"


def _convert_heic(path: Path) -> Path:
    if path.suffix.lower() not in {".heic", ".heif"}:
        return path
    try:
        from pillow_heif import register_heif_opener

        register_heif_opener()
    except Exception:
        return path
    from PIL import Image

    out = path.with_suffix(".jpg")
    with Image.open(path) as image:
        image.convert("RGB").save(out, "JPEG", quality=92)
    path.unlink(missing_ok=True)
    return out


def store_uploads(files: list[tuple[str | None, bytes, str | None]]) -> tuple[Path, int]:
    if not files:
        raise ValueError("Keine Dateien ausgewählt.")
    unique_files, skipped = dedupe_upload_bytes(files)
    if not unique_files:
        raise ValueError("Die Dateien waren leer.")
    folder = uploads_root() / uuid.uuid4().hex[:12]
    folder.mkdir(parents=True, exist_ok=True)
    saved = 0
    for index, (filename, data, content_type) in enumerate(unique_files):
        if not data:
            continue
        ext = _extension(filename, content_type)
        dest = folder / f"{index:04d}{ext}"
        dest.write_bytes(data)
        _convert_heic(dest)
        saved += 1
    if saved == 0:
        raise ValueError("Die Dateien waren leer.")
    return folder, skipped
