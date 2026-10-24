from __future__ import annotations

import argparse
import json

from .jobs import create_job, export_job, get_job
from .models import CreateJobRequest, JobOptions
from .pipeline.ocr import gpu_status


def main() -> None:
    parser = argparse.ArgumentParser(description="digitizeMyBook CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check-gpu", help="Paddle/CUDA-Status anzeigen")

    convert = sub.add_parser("convert", help="Buch konvertieren")
    convert.add_argument("--source", action="append", required=True)
    convert.add_argument("--mode", choices=["fast", "quality"], default="fast")
    convert.add_argument("--title", default="")
    convert.add_argument("--author", default="")
    convert.add_argument("--epub", action="store_true", default=True)
    convert.add_argument("--pdf", action="store_true")
    convert.add_argument("--test-page", action="store_true")

    args = parser.parse_args()
    if args.cmd == "check-gpu":
        print(json.dumps(gpu_status(), indent=2, ensure_ascii=False))
        return

    request = CreateJobRequest(
        sources=args.source,
        options=JobOptions(
            ocr_mode=args.mode,
            title=args.title,
            author=args.author,
            export_epub=bool(args.epub),
            export_pdf=bool(args.pdf),
            test_page_only=bool(args.test_page),
        ),
    )
    job = create_job(request)
    print(f"Job {job.id} gestartet …")
    while True:
        job = get_job(job.id)
        print(f"[{job.status}] {job.progress.message} ({job.progress.current_page}/{job.progress.total_pages})")
        if job.status in {"review", "done", "error"}:
            break
        import time

        time.sleep(1)
    if job.status == "error":
        raise SystemExit(job.error or "Fehler")
    if job.status == "review" and not args.test_page:
        export_job(job.id)
        while True:
            job = get_job(job.id)
            print(f"[{job.status}] {job.progress.message}")
            if job.status in {"done", "error"}:
                break
            import time

            time.sleep(1)
    print(json.dumps(job.public_dict(), indent=2, ensure_ascii=False))
    if job.epub_path:
        print("EPUB:", job.epub_path)
    if job.pdf_path:
        print("PDF:", job.pdf_path)


if __name__ == "__main__":
    main()
