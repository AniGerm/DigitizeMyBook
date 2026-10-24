import { useEffect, useMemo, useRef, useState } from "react";
import {
  BookMetadata,
  Chapter,
  Job,
  JobOptions,
  createJob,
  downloadUrl,
  exportJob,
  getJob,
  health,
  listLibraryJobs,
  deleteJob,
  mergeChapter,
  mergeAllChapters,
  pickPath,
  previewUrl,
  resumeJob,
  saveReview,
  splitChapter,
  subscribeJob,
  uploadFiles,
  uploadJobCover,
  coverPreviewUrl,
  checkUpdate,
  downloadUpdate,
  installUpdate,
  updateStatus,
  type LibraryJob,
  type UpdateStatus,
} from "./api";

const LAST_JOB_KEY = "digitizeMyBook.lastJobId";

const defaultOptions: JobOptions = {
  ocr_mode: "fast",
  language: "deu",
  preprocess: true,
  split_spreads: true,
  skip_blank: true,
  export_epub: true,
  export_pdf: true,
  dpi: 200,
  batch_size: 50,
  title: "",
  author: "",
  test_page_only: false,
};

export default function App() {
  const [sources, setSources] = useState<string[]>([]);
  const [manualPath, setManualPath] = useState("");
  const [options, setOptions] = useState<JobOptions>(defaultOptions);
  const [job, setJob] = useState<Job | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [gpu, setGpu] = useState<{ gpu_available: boolean; device_name: string | null; paddle_installed: boolean } | null>(null);
  const [lanUrls, setLanUrls] = useState<string[]>([]);
  const [selectedChapter, setSelectedChapter] = useState<string | null>(null);
  const [splitPage, setSplitPage] = useState(1);
  const [library, setLibrary] = useState<LibraryJob[]>([]);
  const [updateInfo, setUpdateInfo] = useState<UpdateStatus | null>(null);
  const [coverRev, setCoverRev] = useState(0);
  const [downloadRev, setDownloadRev] = useState(() => Date.now());
  const fileInput = useRef<HTMLInputElement>(null);
  const coverInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    health()
      .then((h) => {
        setGpu(h);
        setLanUrls(h.lan_urls ?? []);
      })
      .catch(() => setGpu({ gpu_available: false, device_name: null, paddle_installed: false }));
  }, []);

  async function loadLibrary() {
    try {
      const items = await listLibraryJobs();
      setLibrary(items);
    } catch {
      setLibrary([]);
    }
  }

  useEffect(() => {
    const last = localStorage.getItem(LAST_JOB_KEY);
    if (!last) return;
    getJob(last)
      .then((loaded) => setJob(loaded))
      .catch(() => localStorage.removeItem(LAST_JOB_KEY));
  }, []);

  useEffect(() => {
    if (job?.id) {
      localStorage.setItem(LAST_JOB_KEY, job.id);
    }
  }, [job?.id]);

  useEffect(() => {
    if (job) return;
    void loadLibrary();
    updateStatus().then(setUpdateInfo).catch(() => setUpdateInfo(null));
  }, [job]);

  useEffect(() => {
    if (job?.status === "done" && (job.epub_path || job.pdf_path)) {
      setDownloadRev(Date.now());
    }
  }, [job?.status, job?.epub_path, job?.pdf_path]);

  useEffect(() => {
    if (!job?.id) return;
    const live = ["queued", "ingesting", "preprocessing", "ocr", "structuring", "exporting"].includes(job.status);
    if (!live) return;
    return subscribeJob(job.id, setJob);
  }, [job?.id, job?.status]);

  const percent = useMemo(() => {
    if (!job?.progress.total_pages) return job?.status === "done" ? 100 : 8;
    return Math.max(6, Math.round((job.progress.current_page / job.progress.total_pages) * 100));
  }, [job]);

  async function addPicked(directories: boolean) {
    setError(null);
    const result = await pickPath(directories);
    setSources(result.paths);
  }

  async function addFromLibrary(list: FileList | null) {
    if (!list?.length) return;
    setBusy(true);
    setError(null);
    try {
      const result = await uploadFiles(list);
      setSources(result.paths);
      if (result.skipped_duplicates) {
        setError(`${result.skipped_duplicates} doppelte Datei(en) im Upload übersprungen.`);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  function addManual() {
    const value = manualPath.trim();
    if (!value) return;
    setSources([value]);
    setManualPath("");
  }

  async function start(testPage: boolean, sourceOverride?: string[]) {
    const activeSources = sourceOverride?.length ? sourceOverride : sources;
    if (!activeSources.length) {
      setError("Keine Quelle gewählt. Bitte PDF oder Ordner erneut auswählen.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const created = await createJob(activeSources, {
        ...options,
        title: job?.metadata.title || options.title,
        author: job?.metadata.author || options.author,
        test_page_only: testPage,
        split_spreads: testPage ? false : options.split_spreads,
      });
      setJob(created);
      setSources(activeSources);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function refresh() {
    if (!job) return;
    setJob(await getJob(job.id));
  }

  async function openJob(id: string) {
    setBusy(true);
    setError(null);
    try {
      const loaded = await getJob(id);
      setJob(loaded);
      setSelectedChapter(loaded.chapters[0]?.id ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function removeFromLibrary(id: string, title: string) {
    const ok = window.confirm(
      `„${title}“ und alle zugehörigen Rohdaten (Scans, OCR, Exporte) auf diesem Rechner unwiderruflich löschen?`,
    );
    if (!ok) return;
    setBusy(true);
    setError(null);
    try {
      await deleteJob(id);
      if (job?.id === id) {
        setJob(null);
        localStorage.removeItem(LAST_JOB_KEY);
      }
      await loadLibrary();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  function statusLabel(status: string): string {
    const labels: Record<string, string> = {
      done: "Fertig",
      review: "Prüfung / Export",
      error: "Fehler",
      exporting: "Export",
      ocr: "OCR läuft",
      ingesting: "Laden",
      preprocessing: "Vorbereitung",
      structuring: "Kapitel",
      queued: "Warteschlange",
    };
    return labels[status] ?? status;
  }

  function formatUpdated(ts: number): string {
    return new Date(ts * 1000).toLocaleString("de-DE", {
      dateStyle: "medium",
      timeStyle: "short",
    });
  }

  async function resume(id: string) {
    setBusy(true);
    setError(null);
    try {
      setJob(await resumeJob(id));
      await loadLibrary();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function onSaveMeta(next: Partial<BookMetadata>, chapters?: Chapter[]) {
    if (!job) return;
    const metadata = {
      ...job.metadata,
      cover_mode: job.metadata.cover_mode ?? "typographic",
      ...next,
    };
    const nextChapters = chapters ?? job.chapters;
    const updated = await saveReview(job.id, nextChapters, metadata);
    setJob(updated);
    if (next.title !== undefined || next.author !== undefined) {
      setCoverRev((v) => v + 1);
    }
  }

  const looksLikeBookSections = useMemo(() => {
    if (!job?.chapters.length) return false;
    const titles = job.chapters.map((c) => c.title.trim());
    const short = titles.filter((t) => t.length <= 50 && t.split(/\s+/).length <= 8);
    return short.length >= Math.max(2, Math.ceil(job.chapters.length * 0.7));
  }, [job?.chapters]);

  const activeChapter = job?.chapters.find((c) => c.id === selectedChapter) ?? job?.chapters[0];

  function leaveJob() {
    setJob(null);
    localStorage.removeItem(LAST_JOB_KEY);
    void loadLibrary();
  }

  async function mergeAll() {
    if (!job) return;
    setJob(await mergeAllChapters(job.id));
  }

  return (
    <div className="shell">
      <header className="site-header">
        <div className="site-header__start">
          {job ? (
            <button type="button" className="brand-link" onClick={leaveJob}>
              digitizeMyBook
            </button>
          ) : (
            <span className="brand-link brand-link--static" aria-current="page">
              digitizeMyBook
            </span>
          )}
          {job && (
            <button type="button" className="secondary home-link" onClick={leaveJob}>
              Home
            </button>
          )}
        </div>
        <div className={`gpu ${gpu?.gpu_available ? "ok" : "bad"}`}>
          {gpu?.gpu_available
            ? `GPU bereit · ${gpu.device_name ?? "CUDA"}`
            : gpu?.paddle_installed
              ? "Paddle ist da, aber keine GPU erkannt"
              : "GPU-Status wird gelesen …"}
        </div>
      </header>

      {!job && updateInfo && (updateInfo.available || updateInfo.phase === "downloading" || updateInfo.phase === "downloaded" || updateInfo.phase === "installing") && (
        <section className="card update-card">
          <h2>Update</h2>
          <p>
            Version {updateInfo.latest_version} ist verfügbar
            {updateInfo.current_version ? ` (installiert: ${updateInfo.current_version})` : ""}.
          </p>
          <div className="row">
            {updateInfo.phase !== "downloaded" && updateInfo.phase !== "installing" && (
              <button type="button" disabled={updateInfo.phase === "downloading"} onClick={() => void downloadUpdate().then(setUpdateInfo)}>
                {updateInfo.phase === "downloading" ? `Laden ${updateInfo.download_percent}%` : "Update laden"}
              </button>
            )}
            {(updateInfo.phase === "downloaded" || updateInfo.phase === "installing") && (
              <button type="button" disabled={updateInfo.phase === "installing"} onClick={() => void installUpdate().then(setUpdateInfo)}>
                {updateInfo.phase === "installing" ? "Installation läuft …" : "Update installieren"}
              </button>
            )}
            <button className="secondary" type="button" onClick={() => void checkUpdate().then(setUpdateInfo)}>Erneut prüfen</button>
          </div>
          {updateInfo.error && <p className="error">{updateInfo.error}</p>}
        </section>
      )}

      {!job && (
        <div className="grid">
          <section className="card">
            <h2>Quelle</h2>
            <p className="muted">Die Dateien bleiben auf diesem Rechner. Es wird nichts in die Cloud geladen.</p>
            <p className="hint">Ein neuer Upload oder Ordner ersetzt die bisherige Quelle.</p>
            <div className="row">
              <button type="button" onClick={() => addPicked(true)}>Ordner wählen</button>
              <button className="secondary" type="button" onClick={() => addPicked(false)}>
                Dateien wählen
              </button>
              <button className="secondary" type="button" disabled={busy} onClick={() => fileInput.current?.click()}>
                Fotos / Mediathek
              </button>
            </div>
            <input
              ref={fileInput}
              className="sr-file"
              type="file"
              accept="image/*,application/pdf,.heic,.heif,.pdf"
              multiple
              onChange={(e) => void addFromLibrary(e.target.files)}
            />
            {lanUrls.length > 0 && (
              <p className="hint">
                Vom iPhone im selben WLAN: {lanUrls.map((url) => (
                  <a key={url} href={url}>{url}</a>
                ))}
                — dann „Fotos / Mediathek“ tippen und aus der Galerie wählen.
              </p>
            )}
            <label className="field">
              Oder Pfad einfügen
              <input
                type="text"
                value={manualPath}
                placeholder="/home/…/scans/mein-buch"
                onChange={(e) => setManualPath(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && addManual()}
              />
            </label>
            <div className="row">
              <button className="secondary" type="button" onClick={addManual}>
                Pfad hinzufügen
              </button>
              <button className="secondary" type="button" onClick={() => setSources([])}>
                Leeren
              </button>
            </div>
            <div className="paths">
              {sources.length === 0 && <div className="muted">Noch keine Quelle gewählt.</div>}
              {sources.map((src) => (
                <div className="path" key={src}>{src}</div>
              ))}
            </div>
          </section>

          <section className="card">
            <h2>Ausgabe</h2>
            <div className="row">
              <label className="field">
                OCR
                <select
                  value={options.ocr_mode}
                  onChange={(e) => setOptions({ ...options, ocr_mode: e.target.value as JobOptions["ocr_mode"] })}
                >
                  <option value="fast">Schnell (zeilenweise, für Buchseiten)</option>
                  <option value="quality">Qualität (VLM, Fallback wenn nötig)</option>
                </select>
              </label>
              <label className="field">
                Sprache
                <select
                  value={options.language}
                  onChange={(e) => setOptions({ ...options, language: e.target.value as JobOptions["language"] })}
                >
                  <option value="deu">Deutsch</option>
                  <option value="eng">Englisch</option>
                  <option value="auto">Auto</option>
                </select>
              </label>
            </div>
            <label className="field">
              Titel
              <input value={options.title} onChange={(e) => setOptions({ ...options, title: e.target.value })} />
            </label>
            <label className="field">
              Autor
              <input value={options.author} onChange={(e) => setOptions({ ...options, author: e.target.value })} />
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={options.export_epub}
                onChange={(e) => setOptions({ ...options, export_epub: e.target.checked })}
              />
              EPUB erzeugen (reflowbar, mit Kapiteln)
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={options.export_pdf}
                onChange={(e) => setOptions({ ...options, export_pdf: e.target.checked })}
              />
              PDF erzeugen (umfließender Text, wie ein Dokument)
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={options.preprocess}
                onChange={(e) => setOptions({ ...options, preprocess: e.target.checked })}
              />
              Seiten begradigen und Kontrast anheben
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={options.split_spreads}
                onChange={(e) => setOptions({ ...options, split_spreads: e.target.checked })}
              />
              Doppelseiten in der Mitte trennen
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={options.skip_blank}
                onChange={(e) => setOptions({ ...options, skip_blank: e.target.checked })}
              />
              Leere Seiten überspringen
            </label>
            <div className="row">
              <label className="field">
                PDF-Auflösung (DPI)
                <input
                  type="number"
                  min={100}
                  max={400}
                  step={25}
                  value={options.dpi}
                  onChange={(e) => setOptions({ ...options, dpi: Number(e.target.value) || 200 })}
                />
              </label>
              <label className="field">
                OCR-Blockgröße
                <input
                  type="number"
                  min={10}
                  max={200}
                  step={10}
                  value={options.batch_size}
                  onChange={(e) => setOptions({ ...options, batch_size: Number(e.target.value) || 50 })}
                />
              </label>
            </div>
            <p className="hint">
              Lange PDFs laufen blockweise: rendern → vorverarbeiten → OCR → speichern. Rohbilder werden
              nach jedem Block gelöscht. Bei Abbruch kann der Job fortgesetzt werden.
            </p>
            {error && <p className="error">{error}</p>}
            <div className="row">
              <button type="button" disabled={!sources.length || busy} onClick={() => start(true)}>
                Testseite
              </button>
              <button className="accent" type="button" disabled={!sources.length || busy} onClick={() => start(false)}>
                Buch digitalisieren
              </button>
            </div>
          </section>

          <section className="card library">
            <h2>Bibliothek</h2>
            <p className="muted">
              Alle Projekte liegen unter{" "}
              <code>~/.local/share/digitizeMyBook/jobs/</code> — ein Reload im Browser löscht nichts.
            </p>
            {library.length === 0 ? (
              <p className="muted">Noch keine Bücher in der Bibliothek.</p>
            ) : (
              library.map((item) => (
                <div className="library-row" key={item.id}>
                  <div className="library-meta">
                    <strong>{item.title}</strong>
                    <p className="muted">
                      {statusLabel(item.status)}
                      {item.total_pages ? ` · ${item.ocr_completed}/${item.total_pages} Seiten OCR` : ""}
                      {item.has_epub ? " · EPUB" : ""}
                      {item.has_pdf ? " · PDF" : ""}
                    </p>
                    <p className="muted small">{formatUpdated(item.updated_at)}</p>
                  </div>
                  <div className="row library-actions">
                    {(item.status === "review" || item.status === "done" || item.status === "exporting") && (
                      <button type="button" disabled={busy} onClick={() => void openJob(item.id)}>
                        Öffnen
                      </button>
                    )}
                    {item.can_resume && (
                      <button type="button" disabled={busy} onClick={() => void resume(item.id)}>
                        OCR fortsetzen
                      </button>
                    )}
                    {item.status === "error" && (
                      <button type="button" disabled={busy} onClick={() => void openJob(item.id)}>
                        Ansehen
                      </button>
                    )}
                    <button
                      className="secondary danger"
                      type="button"
                      disabled={busy}
                      onClick={() => void removeFromLibrary(item.id, item.title)}
                    >
                      Löschen
                    </button>
                  </div>
                </div>
              ))
            )}
            <button className="secondary" type="button" disabled={busy} onClick={() => void loadLibrary()}>
              Liste aktualisieren
            </button>
          </section>
        </div>
      )}

      {job && !["review", "done"].includes(job.status) && job.status !== "error" && (
        <section className="card">
          <h2>{options.test_page_only || job.options.test_page_only ? "Testseite" : "OCR läuft"}</h2>
          <p>{job.progress.message || "Bitte warten …"}</p>
          <div className="progress-wrap">
            <div className="bar"><span style={{ width: `${percent}%` }} /></div>
            <p className="muted">
              {job.progress.current_page}/{job.progress.total_pages || "?"} Seiten · {job.status}
              {job.progress.batch_total ? ` · Block ${job.progress.batch_index}/${job.progress.batch_total}` : ""}
              {job.progress.ocr_completed ? ` · ${job.progress.ocr_completed} OCR fertig` : ""}
            </p>
          </div>
        </section>
      )}

      {job?.status === "error" && (
        <section className="card">
          <h2>Fehler</h2>
          <p className="error">{job.error}</p>
          <div className="row">
            {(job.progress.ocr_completed ?? 0) > 0 &&
              (job.progress.total_pages ?? 0) > (job.progress.ocr_completed ?? 0) && (
                <button type="button" disabled={busy} onClick={() => void resume(job.id)}>
                  OCR fortsetzen ({job.progress.ocr_completed}/{job.progress.total_pages})
                </button>
              )}
            <button className="secondary" type="button" onClick={leaveJob}>Neue Quelle</button>
          </div>
        </section>
      )}

      {job && job.status === "done" && job.options.test_page_only && (
        <section className="card">
          <h2>Testseite</h2>
          {job.pages[0] && <img className="preview" src={previewUrl(job.id, job.pages[0].index)} alt="Testseite" />}
          <pre className="test">{job.test_markdown || "Kein Text erkannt."}</pre>
          <div className="row">
            <button type="button" onClick={() => start(false, job.sources)}>Ganzes Buch starten</button>
            <button className="secondary" type="button" onClick={leaveJob}>Zurück</button>
          </div>
        </section>
      )}

      {job && (job.status === "review" || (job.status === "done" && !job.options.test_page_only) || job.status === "exporting") && (
        <div className="review">
          <section className="card">
            <h2>Kapitel prüfen</h2>
            <p className="muted">
              {job.chapters.length <= 1
                ? "Ein Kapitel erkannt – du kannst direkt exportieren."
                : looksLikeBookSections
                  ? `${job.chapters.length} Buchkapitel erkannt (z. B. fette Kapitelüberschriften). Titel prüfen und exportieren – kein Zusammenführen nötig.`
                  : "Mehrere Abschnitte erkannt. Nur bei echten Kapitelwechseln trennen."}
            </p>
            <label className="field">
              Titel
              <input value={job.metadata.title} onChange={(e) => onSaveMeta({ title: e.target.value })} />
            </label>
            <label className="field">
              Autor
              <input value={job.metadata.author} onChange={(e) => onSaveMeta({ author: e.target.value })} />
            </label>

            <section className="cover-panel">
              <h3>Buchcover (EPUB)</h3>
              <p className="muted">
                Standard ist ein typografisches Cover (Titel/Autor). Alternativ Scan-Seite oder eigenes Bild — ohne neues OCR.
              </p>
              <img
                className="preview cover-thumb"
                src={coverPreviewUrl(job.id, coverRev)}
                alt="Cover-Vorschau"
              />
              <div className="row cover-modes">
                <label>
                  <input
                    type="radio"
                    name="coverMode"
                    checked={(job.metadata.cover_mode || "typographic") === "typographic"}
                    onChange={() => {
                      void onSaveMeta({ cover_mode: "typographic", cover_path: null }).then(() =>
                        setCoverRev((v) => v + 1),
                      );
                    }}
                  />
                  Druckschrift (Titel)
                </label>
                <label>
                  <input
                    type="radio"
                    name="coverMode"
                    checked={job.metadata.cover_mode === "page"}
                    onChange={() => {
                      const page = job.metadata.cover_page ?? 0;
                      void onSaveMeta({ cover_mode: "page", cover_page: page }).then(() =>
                        setCoverRev((v) => v + 1),
                      );
                    }}
                  />
                  Scan-Seite
                </label>
                <label>
                  <input
                    type="radio"
                    name="coverMode"
                    checked={job.metadata.cover_mode === "image"}
                    onChange={() => void onSaveMeta({ cover_mode: "image" })}
                  />
                  Eigenes Bild
                </label>
              </div>
              {job.metadata.cover_mode === "page" && (
                <label className="field">
                  Seite (1–{job.page_count || job.progress.total_pages})
                  <input
                    type="number"
                    min={1}
                    max={job.page_count || job.progress.total_pages || 1}
                    value={(job.metadata.cover_page ?? 0) + 1}
                    onChange={(e) => {
                      const n = Math.max(1, Number(e.target.value) || 1);
                      void onSaveMeta({ cover_mode: "page", cover_page: n - 1 }).then(() =>
                        setCoverRev((v) => v + 1),
                      );
                    }}
                  />
                </label>
              )}
              {job.metadata.cover_mode === "image" && (
                <div className="row">
                  <input
                    ref={coverInput}
                    type="file"
                    accept="image/*"
                    hidden
                    onChange={async (e) => {
                      const file = e.target.files?.[0];
                      if (!file || !job) return;
                      setBusy(true);
                      try {
                        const updated = await uploadJobCover(job.id, file);
                        setJob(updated);
                        setCoverRev((v) => v + 1);
                        if (updated.status === "exporting" || updated.status === "done") {
                          setDownloadRev(Date.now());
                        }
                      } catch (err) {
                        setError(err instanceof Error ? err.message : String(err));
                      } finally {
                        setBusy(false);
                        if (coverInput.current) coverInput.current.value = "";
                      }
                    }}
                  />
                  <button className="secondary" type="button" onClick={() => coverInput.current?.click()}>
                    Cover-Bild hochladen
                  </button>
                </div>
              )}
              {(job.metadata.cover_mode || "typographic") === "typographic" && (
                <p className="muted">Titel und Autor oben anpassen, dann Vorschau aktualisiert sich beim Export.</p>
              )}
            </section>

            {job.chapters.map((chapter, index) => (
              <article className="chapter" key={chapter.id}>
                <header>
                  <strong>S. {chapter.start_page + 1}–{chapter.end_page + 1}</strong>
                  <div className="row">
                    <button className="secondary" type="button" onClick={() => setSelectedChapter(chapter.id)}>
                      Ansehen
                    </button>
                    {index < job.chapters.length - 1 && !looksLikeBookSections && (
                      <button className="secondary" type="button" onClick={async () => setJob(await mergeChapter(job.id, chapter.id))}>
                        Mit nächstem mergen
                      </button>
                    )}
                  </div>
                </header>
                <input
                  type="text"
                  value={chapter.title}
                  onChange={(e) => {
                    const next = job.chapters.map((c) => (c.id === chapter.id ? { ...c, title: e.target.value } : c));
                    onSaveMeta({}, next);
                  }}
                />
              </article>
            ))}
            {activeChapter && (
              <div className="row">
                <label className="field">
                  Kapitel bei Seite teilen
                  <input
                    type="text"
                    value={splitPage}
                    onChange={(e) => setSplitPage(Number(e.target.value) || 1)}
                  />
                </label>
                <button
                  className="secondary"
                  type="button"
                  onClick={async () => setJob(await splitChapter(job.id, activeChapter.id, splitPage - 1))}
                >
                  Teilen
                </button>
              </div>
            )}
            <div className="row">
              <button className="accent" type="button" onClick={async () => setJob(await exportJob(job.id))}>
                {job.status === "done" ? "Erneut exportieren" : "Export starten"}
              </button>
              {job.status === "done" && (
                <span className="muted">
                  Nach Cover-Änderung: „Erneut exportieren“, warten bis „Export fertig“, dann EPUB laden (nicht die alte Datei aus Downloads).
                </span>
              )}
              {job.chapters.length > 1 && !looksLikeBookSections && (
                <button className="secondary" type="button" onClick={() => void mergeAll()}>
                  Alles in ein Kapitel
                </button>
              )}
              <button className="secondary" type="button" onClick={refresh}>Aktualisieren</button>
              <button className="secondary" type="button" onClick={leaveJob}>Neues Buch</button>
            </div>
            {job.epub_path && job.status === "done" && (
              <p><a href={downloadUrl(job.id, "epub", downloadRev)}>EPUB herunterladen</a></p>
            )}
            {job.pdf_path && job.status === "done" && (
              <p><a href={downloadUrl(job.id, "pdf", downloadRev)}>PDF herunterladen</a></p>
            )}
            {job.status === "exporting" && <p className="muted">{job.progress.message}</p>}
          </section>
          <section className="card">
            <h2>Vorschau</h2>
            {activeChapter ? (
              <img className="preview" src={previewUrl(job.id, activeChapter.start_page)} alt="Kapitelvorschau" />
            ) : (
              <p className="muted">Keine Kapitel erkannt.</p>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
