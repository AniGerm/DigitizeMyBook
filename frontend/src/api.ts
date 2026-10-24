export type OcrMode = "fast" | "quality";

export type JobOptions = {
  ocr_mode: OcrMode;
  language: "deu" | "eng" | "auto";
  preprocess: boolean;
  split_spreads: boolean;
  skip_blank: boolean;
  export_epub: boolean;
  export_pdf: boolean;
  dpi: number;
  batch_size: number;
  title: string;
  author: string;
  test_page_only: boolean;
};

export type ResumableJob = {
  id: string;
  title: string;
  status: string;
  ocr_completed: number;
  total_pages: number;
  message: string;
};

export type LibraryJob = ResumableJob & {
  updated_at: number;
  has_epub: boolean;
  has_pdf: boolean;
  can_resume: boolean;
  test_page_only: boolean;
};

export type Chapter = {
  id: string;
  title: string;
  start_page: number;
  end_page: number;
};

export type CoverMode = "typographic" | "page" | "image";

export type BookMetadata = {
  title: string;
  author: string;
  language: string;
  cover_mode: CoverMode;
  cover_page: number;
  cover_path: string | null;
};

export type Job = {
  id: string;
  status: string;
  sources: string[];
  options: JobOptions;
  progress: {
    phase: string;
    current_page: number;
    total_pages: number;
    message: string;
    ocr_completed?: number;
    batch_index?: number;
    batch_total?: number;
  };
  error: string | null;
  chapters: Chapter[];
  metadata: BookMetadata;
  epub_path: string | null;
  pdf_path: string | null;
  test_markdown: string | null;
  test_image: string | null;
  page_count: number;
  pages: { index: number; skipped: boolean; width: number; height: number }[];
};

async function parse<T>(res: Response | Promise<Response>): Promise<T> {
  const resolved = await res;
  if (!resolved.ok) {
    const text = await resolved.text();
    throw new Error(text || resolved.statusText);
  }
  return resolved.json() as Promise<T>;
}

export function health() {
  return parse<{
    ok: boolean;
    gpu_available: boolean;
    device_name: string | null;
    paddle_installed: boolean;
    error: string | null;
    lan_urls?: string[];
    version?: string;
  }>(awaitable("/api/health"));
}

export type UpdateStatus = {
  phase: string;
  current_version: string | null;
  latest_version: string | null;
  available: boolean;
  download_percent: number;
  error: string | null;
  repo: string;
};

export function updateStatus() {
  return parse<UpdateStatus>(awaitable("/api/updates"));
}

export function checkUpdate() {
  return parse<UpdateStatus>(awaitable("/api/updates/check", { method: "POST" }));
}

export function downloadUpdate() {
  return parse<UpdateStatus>(awaitable("/api/updates/download", { method: "POST" }));
}

export function installUpdate() {
  return parse<UpdateStatus>(awaitable("/api/updates/install", { method: "POST" }));
}

function awaitable(url: string, init?: RequestInit) {
  return fetch(url, init);
}

export function createJob(sources: string[], options: JobOptions) {
  return parse<Job>(
    awaitable("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sources, options }),
    }),
  );
}

export function getJob(id: string) {
  return parse<Job>(awaitable(`/api/jobs/${id}`));
}

export function listResumableJobs() {
  return parse<ResumableJob[]>(awaitable("/api/jobs/resumable"));
}

export function listLibraryJobs() {
  return parse<LibraryJob[]>(awaitable("/api/jobs"));
}

export function deleteJob(id: string) {
  return parse<{ ok: boolean }>(
    awaitable(`/api/jobs/${id}`, {
      method: "DELETE",
    }),
  );
}

export function resumeJob(id: string) {
  return parse<Job>(awaitable(`/api/jobs/${id}/resume`, { method: "POST" }));
}

export function pickPath(directories: boolean) {
  return parse<{ paths: string[] }>(
    awaitable("/api/pick", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ directories }),
    }),
  );
}

export async function uploadFiles(files: FileList | File[]) {
  const body = new FormData();
  Array.from(files).forEach((file) => body.append("files", file));
  return parse<{ paths: string[]; count: number; skipped_duplicates?: number }>(
    awaitable("/api/uploads", {
      method: "POST",
      body,
    }),
  );
}

export function saveReview(id: string, chapters: Chapter[], metadata: BookMetadata) {
  return parse<Job>(
    awaitable(`/api/jobs/${id}/review`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chapters, metadata }),
    }),
  );
}

export function mergeChapter(id: string, chapter_id: string) {
  return parse<Job>(
    awaitable(`/api/jobs/${id}/merge`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chapter_id }),
    }),
  );
}

export function mergeAllChapters(id: string) {
  return parse<Job>(awaitable(`/api/jobs/${id}/merge-all`, { method: "POST" }));
}

export function splitChapter(id: string, chapter_id: string, at_page: number) {
  return parse<Job>(
    awaitable(`/api/jobs/${id}/split`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chapter_id, at_page }),
    }),
  );
}

export function exportJob(id: string) {
  return parse<Job>(awaitable(`/api/jobs/${id}/export`, { method: "POST" }));
}

export function previewUrl(jobId: string, page: number) {
  return `/api/jobs/${jobId}/preview/${page}`;
}

export function coverPreviewUrl(jobId: string, cacheKey = 0) {
  return `/api/jobs/${jobId}/cover?v=${cacheKey}`;
}

export async function uploadJobCover(jobId: string, file: File) {
  const body = new FormData();
  body.append("file", file);
  return parse<Job>(
    awaitable(`/api/jobs/${jobId}/cover`, {
      method: "POST",
      body,
    }),
  );
}

export function downloadUrl(jobId: string, kind: "epub" | "pdf", cacheBust?: number) {
  const v = cacheBust ?? Date.now();
  return `/api/jobs/${jobId}/download/${kind}?v=${v}`;
}

export function subscribeJob(id: string, onData: (job: Job) => void): () => void {
  const source = new EventSource(`/api/jobs/${id}/events`);
  source.onmessage = (event) => {
    onData(JSON.parse(event.data) as Job);
  };
  return () => source.close();
}
