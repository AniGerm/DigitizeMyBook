from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


CoverMode = Literal["typographic", "page", "image"]
OcrMode = Literal["fast", "quality"]
LanguageCode = Literal["deu", "eng", "auto"]
JobStatus = Literal[
    "queued",
    "ingesting",
    "preprocessing",
    "ocr",
    "structuring",
    "review",
    "exporting",
    "done",
    "error",
]


class JobOptions(BaseModel):
    ocr_mode: OcrMode = "fast"
    language: LanguageCode = "deu"
    preprocess: bool = True
    split_spreads: bool = True
    skip_blank: bool = True
    export_epub: bool = True
    export_pdf: bool = False
    dpi: int = 200
    title: str = ""
    author: str = ""
    test_page_only: bool = False
    batch_size: int = 50
    checkpoint_pages: bool = True


class CreateJobRequest(BaseModel):
    sources: list[str] = Field(min_length=1)
    options: JobOptions = Field(default_factory=JobOptions)


class Block(BaseModel):
    label: str
    content: str = ""
    bbox: list[float] = Field(default_factory=list)
    order: int | None = None
    image_path: str | None = None


class PageResult(BaseModel):
    index: int
    source_path: str
    image_path: str
    width: int = 0
    height: int = 0
    skipped: bool = False
    skip_reason: str | None = None
    markdown: str = ""
    blocks: list[Block] = Field(default_factory=list)
    spread_id: int | None = None
    source_index: int | None = None
    printed_page: int | None = None


class Chapter(BaseModel):
    id: str
    title: str
    start_page: int
    end_page: int


class SourcePlanEntry(BaseModel):
    source_index: int
    source_path: str
    pdf_page: int | None = None


class SourcePlan(BaseModel):
    entries: list[SourcePlanEntry] = Field(default_factory=list)
    skipped_duplicates: int = 0

    @property
    def source_count(self) -> int:
        return len(self.entries)


class BookMetadata(BaseModel):
    title: str = "Unbenanntes Buch"
    author: str = ""
    language: str = "de"
    cover_mode: CoverMode = "typographic"
    cover_page: int = 0
    cover_path: str | None = None


class JobProgress(BaseModel):
    phase: str = "queued"
    current_page: int = 0
    total_pages: int = 0
    message: str = ""
    ocr_completed: int = 0
    batch_index: int = 0
    batch_total: int = 0


class JobState(BaseModel):
    id: str
    status: JobStatus = "queued"
    sources: list[str]
    options: JobOptions
    progress: JobProgress = Field(default_factory=JobProgress)
    error: str | None = None
    pages: list[PageResult] = Field(default_factory=list)
    chapters: list[Chapter] = Field(default_factory=list)
    metadata: BookMetadata = Field(default_factory=BookMetadata)
    epub_path: str | None = None
    pdf_path: str | None = None
    test_markdown: str | None = None
    test_image: str | None = None

    def public_dict(self) -> dict:
        data = self.model_dump()
        data["page_count"] = len(self.pages)
        data["pages"] = [
            {
                "index": p.index,
                "width": p.width,
                "height": p.height,
                "skipped": p.skipped,
                "source_path": p.source_path,
                "image_path": p.image_path,
            }
            for p in self.pages
        ]
        return data
