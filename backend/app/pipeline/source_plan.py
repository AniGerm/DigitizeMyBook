from __future__ import annotations

from pathlib import Path

from ..models import SourcePlan, SourcePlanEntry
from .dedupe import image_file_hash
from .ingest import PDF_EXT, collect_input_files, pdf_page_count


def build_source_plan(sources: list[str], max_pages: int | None = None) -> SourcePlan:
    files = collect_input_files(sources)
    entries: list[SourcePlanEntry] = []
    seen_hashes: set[str] = set()
    skipped_duplicates = 0
    source_index = 0

    for file in files:
        if max_pages is not None and source_index >= max_pages:
            break
        if file.suffix.lower() == PDF_EXT:
            total = pdf_page_count(file)
            for pdf_page in range(total):
                if max_pages is not None and source_index >= max_pages:
                    break
                entries.append(
                    SourcePlanEntry(
                        source_index=source_index,
                        source_path=str(file),
                        pdf_page=pdf_page,
                    )
                )
                source_index += 1
            continue

        digest = image_file_hash(file)
        if digest and digest in seen_hashes:
            skipped_duplicates += 1
            continue
        if digest:
            seen_hashes.add(digest)
        entries.append(
            SourcePlanEntry(
                source_index=source_index,
                source_path=str(file),
                pdf_page=None,
            )
        )
        source_index += 1

    if not entries:
        raise ValueError("Es konnten keine Seiten geplant werden.")
    return SourcePlan(entries=entries, skipped_duplicates=skipped_duplicates)


def should_stream(plan: SourcePlan, *, test_page_only: bool, checkpoint_pages: bool) -> bool:
    if test_page_only or not checkpoint_pages:
        return False
    return plan.source_count > 1
