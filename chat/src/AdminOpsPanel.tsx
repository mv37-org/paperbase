import { useEffect, useState } from "react";

import {
  getBrowserAdminPaperAsset,
  getBrowserAdminPaperFile,
  getBrowserPaperPreview,
  loadBrowserAdminJobs,
  loadBrowserAdminPaperFiles,
  loadBrowserAdminPapers,
  queueBrowserAdminEnrichMissingMetadata,
  queueBrowserAdminHarvestRange,
  queueBrowserAdminHarvestTopCited,
  queueBrowserAdminHarvestVenue,
  queueBrowserAdminPaperIndex,
  retryBrowserAdminJob,
} from "./api";
import type {
  FileContentResponse,
  FileObject,
  JobResponse,
  PaperAssetResponse,
  PaperPreviewResponse,
  PaperResponse,
} from "./types";

type AdminOpsPanelProps = {
  onError: (message: string) => void;
  onNote: (message: string) => void;
};

const JOB_STATUS_OPTIONS = ["all", "pending", "running", "completed", "dead"] as const;
const PAPER_STATUS_OPTIONS = ["all", "discovered", "indexing", "indexed"] as const;
const TEXT_FILE_EXTENSIONS = new Set([
  ".csv",
  ".html",
  ".json",
  ".log",
  ".md",
  ".py",
  ".tex",
  ".toml",
  ".tsv",
  ".txt",
  ".xml",
  ".yaml",
  ".yml",
]);

function parseCsvList(value: string) {
  return value
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function parseOptionalInteger(value: FormDataEntryValue | null) {
  const text = String(value ?? "").trim();
  if (!text) {
    return null;
  }
  return Number.parseInt(text, 10);
}

function formatTimestamp(value?: string | null) {
  if (!value) {
    return "Not yet";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString();
}

function jobSummary(job: JobResponse) {
  return `${job.job_type} · ${job.status}`;
}

function isProbablyTextFile(file: FileObject) {
  const contentType = (file.content_type ?? "").split(";", 1)[0].trim().toLowerCase();
  if (contentType.startsWith("text/")) {
    return true;
  }
  if (["application/json", "application/xml", "application/x-yaml"].includes(contentType)) {
    return true;
  }
  const suffix = file.path.includes(".")
    ? file.path.slice(file.path.lastIndexOf(".")).toLowerCase()
    : "";
  return TEXT_FILE_EXTENSIONS.has(suffix);
}

function assetKind(asset: PaperAssetResponse | null) {
  const contentType = (asset?.content_type ?? "").toLowerCase();
  if (contentType.startsWith("image/")) {
    return "image";
  }
  if (contentType === "application/pdf") {
    return "pdf";
  }
  return "file";
}

function panelErrorMessage(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}

export function AdminOpsPanel({ onError, onNote }: AdminOpsPanelProps) {
  const [jobStatus, setJobStatus] = useState<(typeof JOB_STATUS_OPTIONS)[number]>("all");
  const [jobs, setJobs] = useState<JobResponse[]>([]);
  const [jobsLoading, setJobsLoading] = useState(false);
  const [jobsError, setJobsError] = useState("");

  const [paperQueryInput, setPaperQueryInput] = useState("");
  const [paperQuery, setPaperQuery] = useState("");
  const [paperStatus, setPaperStatus] = useState<(typeof PAPER_STATUS_OPTIONS)[number]>("indexed");
  const [papers, setPapers] = useState<PaperResponse[]>([]);
  const [papersLoading, setPapersLoading] = useState(false);
  const [papersError, setPapersError] = useState("");
  const [selectedPaperId, setSelectedPaperId] = useState<string | null>(null);

  const [paperFiles, setPaperFiles] = useState<FileObject[]>([]);
  const [paperFilesLoading, setPaperFilesLoading] = useState(false);
  const [paperFilesError, setPaperFilesError] = useState("");
  const [paperPreview, setPaperPreview] = useState<PaperPreviewResponse | null>(null);

  const [selectedFilePath, setSelectedFilePath] = useState<string | null>(null);
  const [selectedFileContent, setSelectedFileContent] = useState<FileContentResponse | null>(null);
  const [selectedAsset, setSelectedAsset] = useState<PaperAssetResponse | null>(null);
  const [selectedFileLoading, setSelectedFileLoading] = useState(false);
  const [selectedFileError, setSelectedFileError] = useState("");

  const selectedPaper = papers.find((paper) => paper.paper_id === selectedPaperId) ?? null;

  async function refreshJobs(status = jobStatus) {
    setJobsLoading(true);
    setJobsError("");
    try {
      setJobs(await loadBrowserAdminJobs(status, 75));
    } catch (error) {
      setJobsError(panelErrorMessage(error, "Jobs could not be loaded."));
    } finally {
      setJobsLoading(false);
    }
  }

  async function refreshPapers(nextQuery = paperQuery, nextStatus = paperStatus) {
    setPapersLoading(true);
    setPapersError("");
    try {
      setPapers(await loadBrowserAdminPapers(nextQuery, nextStatus, 50));
    } catch (error) {
      setPapersError(panelErrorMessage(error, "Papers could not be loaded."));
    } finally {
      setPapersLoading(false);
    }
  }

  useEffect(() => {
    void refreshJobs();
  }, [jobStatus]);

  useEffect(() => {
    void refreshPapers();
  }, [paperQuery, paperStatus]);

  useEffect(() => {
    if (papers.length === 0) {
      setSelectedPaperId(null);
      return;
    }
    if (!selectedPaperId || !papers.some((paper) => paper.paper_id === selectedPaperId)) {
      setSelectedPaperId(papers[0]?.paper_id ?? null);
    }
  }, [papers, selectedPaperId]);

  useEffect(() => {
    if (!selectedPaper) {
      setPaperFiles([]);
      setPaperFilesError("");
      setPaperPreview(null);
      setSelectedFilePath(null);
      setSelectedFileContent(null);
      setSelectedAsset(null);
      setSelectedFileError("");
      return;
    }

    let cancelled = false;
    setPaperFilesLoading(true);
    setPaperFilesError("");
    setSelectedFilePath(null);
    setSelectedFileContent(null);
    setSelectedAsset(null);
    setSelectedFileError("");

    void (async () => {
      try {
        const [files, preview] = await Promise.all([
          loadBrowserAdminPaperFiles(selectedPaper.paper_id),
          getBrowserPaperPreview(selectedPaper.paper_id).catch(() => null),
        ]);
        if (cancelled) {
          return;
        }
        setPaperFiles(files.files);
        setPaperPreview(preview);
      } catch (error) {
        if (cancelled) {
          return;
        }
        setPaperFilesError(panelErrorMessage(error, "Paper artifacts could not be loaded."));
      } finally {
        if (!cancelled) {
          setPaperFilesLoading(false);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [selectedPaper]);

  async function openFile(file: FileObject) {
    if (!selectedPaper) {
      return;
    }
    setSelectedFilePath(file.path);
    setSelectedFileLoading(true);
    setSelectedFileError("");
    setSelectedFileContent(null);
    setSelectedAsset(null);
    try {
      if (isProbablyTextFile(file)) {
        try {
          const response = await getBrowserAdminPaperFile(selectedPaper.paper_id, file.path);
          setSelectedFileContent(response);
          return;
        } catch (error) {
          const message = panelErrorMessage(error, "");
          if (!message.toLowerCase().includes("not a utf-8 text artifact")) {
            throw error;
          }
        }
      }
      const asset = await getBrowserAdminPaperAsset(selectedPaper.paper_id, file.path);
      setSelectedAsset(asset);
    } catch (error) {
      const message = panelErrorMessage(error, "Paper artifact could not be opened.");
      setSelectedFileError(message);
      onError(message);
    } finally {
      setSelectedFileLoading(false);
    }
  }

  return (
    <section
      className="settings-section admin-ops-section"
      role="tabpanel"
      id="settings-panel-admin"
      aria-labelledby="settings-tab-admin"
    >
      <div className="settings-section-heading">
        <h3 className="settings-section-title">Admin</h3>
        <p className="settings-section-copy">
          Queue backfills, inspect jobs, and browse paper artifacts in storage.
        </p>
      </div>

      <div className="admin-ops-grid">
        <section className="admin-ops-card">
          <div className="admin-ops-card-heading">
            <h4>Queue jobs</h4>
            <p>Single-paper indexing plus range, top-cited, venue, and enrichment backfills.</p>
          </div>
          <div className="admin-ops-form-grid">
            <form
              className="admin-ops-form"
              onSubmit={async (event) => {
                event.preventDefault();
                const formData = new FormData(event.currentTarget);
                const arxivId = String(formData.get("arxiv_id") ?? "").trim();
                if (!arxivId) {
                  onError("Enter an arXiv id.");
                  return;
                }
                try {
                  const job = await queueBrowserAdminPaperIndex(
                    arxivId,
                    formData.get("force") === "on",
                  );
                  onNote(`Queued ${jobSummary(job)}.`);
                  await refreshJobs();
                } catch (error) {
                  onError(panelErrorMessage(error, "Paper indexing could not be queued."));
                }
              }}
            >
              <div className="admin-ops-card-title">Single paper</div>
              <label className="settings-field">
                <span className="settings-field-label">arXiv ID</span>
                <input name="arxiv_id" placeholder="2401.01234" />
              </label>
              <label className="admin-ops-checkbox">
                <input name="force" type="checkbox" />
                <span>Force reindex</span>
              </label>
              <button className="primary-button admin-ops-submit" type="submit">
                Queue paper
              </button>
            </form>

            <form
              className="admin-ops-form"
              onSubmit={async (event) => {
                event.preventDefault();
                const formData = new FormData(event.currentTarget);
                try {
                  const job = await queueBrowserAdminHarvestRange({
                    categories: parseCsvList(String(formData.get("categories") ?? "")),
                    start_date: String(formData.get("start_date") ?? "").trim(),
                    end_date: String(formData.get("end_date") ?? "").trim(),
                    limit_per_window: parseOptionalInteger(formData.get("limit_per_window")),
                    chunk_months: Number.parseInt(String(formData.get("chunk_months") ?? "3"), 10),
                    source: "arxiv",
                  });
                  onNote(`Queued ${jobSummary(job)}.`);
                  await refreshJobs();
                } catch (error) {
                  onError(panelErrorMessage(error, "Range harvest could not be queued."));
                }
              }}
            >
              <div className="admin-ops-card-title">Date range</div>
              <label className="settings-field">
                <span className="settings-field-label">Categories</span>
                <textarea
                  name="categories"
                  rows={3}
                  placeholder={"cs.LG, cs.CL\nLeave blank for the default AI/ML set."}
                />
              </label>
              <div className="admin-ops-inline-fields">
                <label className="settings-field">
                  <span className="settings-field-label">Start date</span>
                  <input name="start_date" type="date" required />
                </label>
                <label className="settings-field">
                  <span className="settings-field-label">End date</span>
                  <input name="end_date" type="date" required />
                </label>
              </div>
              <div className="admin-ops-inline-fields">
                <label className="settings-field">
                  <span className="settings-field-label">Limit / window</span>
                  <input name="limit_per_window" type="number" defaultValue="1000" min={1} />
                </label>
                <label className="settings-field">
                  <span className="settings-field-label">Chunk months</span>
                  <input name="chunk_months" type="number" defaultValue="3" min={1} max={24} />
                </label>
              </div>
              <button className="primary-button admin-ops-submit" type="submit">
                Queue range
              </button>
            </form>

            <form
              className="admin-ops-form"
              onSubmit={async (event) => {
                event.preventDefault();
                const formData = new FormData(event.currentTarget);
                try {
                  const job = await queueBrowserAdminHarvestTopCited({
                    min_citations: Number.parseInt(
                      String(formData.get("min_citations") ?? "100"),
                      10,
                    ),
                    max_papers: parseOptionalInteger(formData.get("max_papers")),
                    chunk_size: Number.parseInt(String(formData.get("chunk_size") ?? "50"), 10),
                  });
                  onNote(`Queued ${jobSummary(job)}.`);
                  await refreshJobs();
                } catch (error) {
                  onError(panelErrorMessage(error, "Top-cited harvest could not be queued."));
                }
              }}
            >
              <div className="admin-ops-card-title">Top cited</div>
              <div className="admin-ops-inline-fields">
                <label className="settings-field">
                  <span className="settings-field-label">Min citations</span>
                  <input name="min_citations" type="number" defaultValue="100" min={0} />
                </label>
                <label className="settings-field">
                  <span className="settings-field-label">Chunk size</span>
                  <input name="chunk_size" type="number" defaultValue="50" min={1} max={500} />
                </label>
              </div>
              <label className="settings-field">
                <span className="settings-field-label">Max papers</span>
                <input
                  name="max_papers"
                  type="number"
                  placeholder="Leave blank for all available"
                  min={1}
                />
              </label>
              <button className="primary-button admin-ops-submit" type="submit">
                Queue top cited
              </button>
            </form>

            <form
              className="admin-ops-form"
              onSubmit={async (event) => {
                event.preventDefault();
                const formData = new FormData(event.currentTarget);
                try {
                  const job = await queueBrowserAdminHarvestVenue({
                    venue: String(formData.get("venue") ?? "").trim(),
                    year: Number.parseInt(String(formData.get("year") ?? ""), 10),
                    max_papers: parseOptionalInteger(formData.get("max_papers")),
                    chunk_size: Number.parseInt(String(formData.get("chunk_size") ?? "50"), 10),
                  });
                  onNote(`Queued ${jobSummary(job)}.`);
                  await refreshJobs();
                } catch (error) {
                  onError(panelErrorMessage(error, "Venue harvest could not be queued."));
                }
              }}
            >
              <div className="admin-ops-card-title">Venue year</div>
              <div className="admin-ops-inline-fields">
                <label className="settings-field">
                  <span className="settings-field-label">Venue</span>
                  <input name="venue" placeholder="ICLR" required />
                </label>
                <label className="settings-field">
                  <span className="settings-field-label">Year</span>
                  <input
                    name="year"
                    type="number"
                    placeholder="2024"
                    min={1900}
                    max={2100}
                    required
                  />
                </label>
              </div>
              <div className="admin-ops-inline-fields">
                <label className="settings-field">
                  <span className="settings-field-label">Max papers</span>
                  <input name="max_papers" type="number" min={1} />
                </label>
                <label className="settings-field">
                  <span className="settings-field-label">Chunk size</span>
                  <input name="chunk_size" type="number" defaultValue="50" min={1} max={500} />
                </label>
              </div>
              <button className="primary-button admin-ops-submit" type="submit">
                Queue venue
              </button>
            </form>

            <form
              className="admin-ops-form"
              onSubmit={async (event) => {
                event.preventDefault();
                const formData = new FormData(event.currentTarget);
                try {
                  const job = await queueBrowserAdminEnrichMissingMetadata(
                    Number.parseInt(String(formData.get("limit") ?? "50000"), 10),
                    Number.parseInt(String(formData.get("min_age_seconds") ?? "3600"), 10),
                  );
                  onNote(`Queued ${jobSummary(job)}.`);
                  await refreshJobs();
                } catch (error) {
                  onError(panelErrorMessage(error, "Metadata backfill could not be queued."));
                }
              }}
            >
              <div className="admin-ops-card-title">Metadata enrichment</div>
              <div className="admin-ops-inline-fields">
                <label className="settings-field">
                  <span className="settings-field-label">Limit</span>
                  <input name="limit" type="number" defaultValue="50000" min={1} />
                </label>
                <label className="settings-field">
                  <span className="settings-field-label">Min age (sec)</span>
                  <input name="min_age_seconds" type="number" defaultValue="3600" min={0} />
                </label>
              </div>
              <button className="primary-button admin-ops-submit" type="submit">
                Queue enrichment
              </button>
            </form>
          </div>
        </section>

        <section className="admin-ops-card">
          <div className="admin-ops-card-heading">
            <h4>Jobs</h4>
            <p>Recent queue state, retry counts, payloads, and terminal errors.</p>
          </div>
          <div className="admin-ops-toolbar">
            <label className="settings-field admin-ops-toolbar-field">
              <span className="settings-field-label">Status</span>
              <select
                value={jobStatus}
                onChange={(event) => setJobStatus(event.target.value as typeof jobStatus)}
              >
                {JOB_STATUS_OPTIONS.map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            </label>
            <button className="secondary-button" type="button" onClick={() => void refreshJobs()}>
              Refresh jobs
            </button>
          </div>
          {jobsError ? <div className="admin-ops-inline-error">{jobsError}</div> : null}
          {jobsLoading ? <div className="admin-ops-inline-note">Loading jobs…</div> : null}
          <div className="admin-ops-table-shell">
            <table className="admin-ops-table">
              <thead>
                <tr>
                  <th>Job</th>
                  <th>Status</th>
                  <th>Attempts</th>
                  <th>Updated</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((job) => (
                  <tr key={job.job_id}>
                    <td>
                      <div className="admin-ops-job-primary">{job.job_type}</div>
                      <div className="admin-ops-job-secondary">{job.job_id}</div>
                      <details className="admin-ops-details">
                        <summary>Payload and errors</summary>
                        <pre>{JSON.stringify(job.payload, null, 2)}</pre>
                        {job.error ? <pre>{job.error}</pre> : null}
                      </details>
                    </td>
                    <td>
                      <span className={`admin-ops-status-pill status-${job.status}`}>
                        {job.status}
                      </span>
                    </td>
                    <td>
                      {job.attempts}/{job.max_attempts}
                    </td>
                    <td>{formatTimestamp(job.updated_at)}</td>
                    <td>
                      {["dead", "failed"].includes(job.status) ? (
                        <button
                          className="secondary-button admin-ops-table-button"
                          type="button"
                          onClick={async () => {
                            try {
                              const retried = await retryBrowserAdminJob(job.job_id);
                              onNote(`Queued retry for ${jobSummary(retried)}.`);
                              await refreshJobs();
                            } catch (error) {
                              onError(panelErrorMessage(error, "Retry could not be queued."));
                            }
                          }}
                        >
                          Retry
                        </button>
                      ) : (
                        <span className="admin-ops-inline-note">None</span>
                      )}
                    </td>
                  </tr>
                ))}
                {jobs.length === 0 && !jobsLoading ? (
                  <tr>
                    <td colSpan={5}>
                      <div className="admin-ops-inline-note">
                        No jobs matched the current filter.
                      </div>
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </section>

        <section className="admin-ops-card">
          <div className="admin-ops-card-heading">
            <h4>Papers and artifacts</h4>
            <p>Search indexed papers, inspect previews, and open stored artifacts.</p>
          </div>
          <form
            className="admin-ops-toolbar admin-ops-toolbar-search"
            onSubmit={(event) => {
              event.preventDefault();
              setPaperQuery(paperQueryInput.trim());
            }}
          >
            <label className="settings-field admin-ops-toolbar-field admin-ops-toolbar-field-wide">
              <span className="settings-field-label">Search</span>
              <input
                value={paperQueryInput}
                onChange={(event) => setPaperQueryInput(event.target.value)}
                placeholder="Title, arXiv id, or author"
              />
            </label>
            <label className="settings-field admin-ops-toolbar-field">
              <span className="settings-field-label">Index status</span>
              <select
                value={paperStatus}
                onChange={(event) => setPaperStatus(event.target.value as typeof paperStatus)}
              >
                {PAPER_STATUS_OPTIONS.map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            </label>
            <button className="secondary-button" type="submit">
              Search papers
            </button>
          </form>
          {papersError ? <div className="admin-ops-inline-error">{papersError}</div> : null}
          {papersLoading ? <div className="admin-ops-inline-note">Loading papers…</div> : null}
          <div className="admin-ops-paper-layout">
            <div className="admin-ops-paper-list">
              {papers.map((paper) => (
                <button
                  key={paper.paper_id}
                  type="button"
                  className={`admin-ops-paper-row${paper.paper_id === selectedPaperId ? " active" : ""}`}
                  onClick={() => setSelectedPaperId(paper.paper_id)}
                >
                  <span className="admin-ops-paper-title">{paper.title || paper.arxiv_id}</span>
                  <span className="admin-ops-paper-meta">{paper.arxiv_id}</span>
                  <span className="admin-ops-paper-meta">
                    {paper.index_status} ·{" "}
                    {paper.indexed_at ? formatTimestamp(paper.indexed_at) : "Not indexed"}
                  </span>
                </button>
              ))}
              {papers.length === 0 && !papersLoading ? (
                <div className="admin-ops-inline-note">No papers matched the current filter.</div>
              ) : null}
            </div>

            <div className="admin-ops-paper-detail">
              {selectedPaper ? (
                <>
                  <div className="admin-ops-paper-header">
                    <div>
                      <h5>{selectedPaper.title || selectedPaper.arxiv_id}</h5>
                      <p>{selectedPaper.authors || "Unknown authors"}</p>
                      <p>
                        {selectedPaper.arxiv_id} · {selectedPaper.index_status} ·{" "}
                        {selectedPaper.citation_count} cites
                      </p>
                    </div>
                    {paperPreview ? (
                      <img
                        className="admin-ops-preview"
                        src={paperPreview.image_url}
                        alt={`Preview for ${selectedPaper.title || selectedPaper.arxiv_id}`}
                      />
                    ) : null}
                  </div>

                  <div className="admin-ops-file-section">
                    <div className="admin-ops-subheading">Files</div>
                    {paperFilesError ? (
                      <div className="admin-ops-inline-error">{paperFilesError}</div>
                    ) : null}
                    {paperFilesLoading ? (
                      <div className="admin-ops-inline-note">Loading files…</div>
                    ) : null}
                    <div className="admin-ops-file-list">
                      {paperFiles.map((file) => (
                        <button
                          key={file.path}
                          type="button"
                          className={`admin-ops-file-row${file.path === selectedFilePath ? " active" : ""}`}
                          onClick={() => void openFile(file)}
                        >
                          <span>{file.path}</span>
                          <span>{file.content_type || `${file.size} bytes`}</span>
                        </button>
                      ))}
                    </div>
                  </div>

                  <div className="admin-ops-file-section">
                    <div className="admin-ops-subheading">Viewer</div>
                    {selectedFileLoading ? (
                      <div className="admin-ops-inline-note">Opening artifact…</div>
                    ) : null}
                    {selectedFileError ? (
                      <div className="admin-ops-inline-error">{selectedFileError}</div>
                    ) : null}
                    {selectedFileContent ? (
                      <pre className="admin-ops-pre">{selectedFileContent.content}</pre>
                    ) : null}
                    {selectedAsset && assetKind(selectedAsset) === "image" ? (
                      <a
                        href={selectedAsset.url}
                        target="_blank"
                        rel="noreferrer"
                        className="admin-ops-asset-link"
                      >
                        <img
                          className="admin-ops-asset-image"
                          src={selectedAsset.url}
                          alt={selectedAsset.path}
                        />
                      </a>
                    ) : null}
                    {selectedAsset && assetKind(selectedAsset) === "pdf" ? (
                      <div className="admin-ops-asset-shell">
                        <iframe
                          title={selectedAsset.path}
                          src={selectedAsset.url}
                          className="admin-ops-asset-frame"
                        />
                        <a
                          href={selectedAsset.url}
                          target="_blank"
                          rel="noreferrer"
                          className="admin-ops-asset-link"
                        >
                          Open PDF in new tab
                        </a>
                      </div>
                    ) : null}
                    {selectedAsset && assetKind(selectedAsset) === "file" ? (
                      <a
                        href={selectedAsset.url}
                        target="_blank"
                        rel="noreferrer"
                        className="admin-ops-asset-link"
                      >
                        Open file
                      </a>
                    ) : null}
                    {!selectedFileLoading &&
                    !selectedFileError &&
                    !selectedFileContent &&
                    !selectedAsset &&
                    selectedPaper ? (
                      <div className="admin-ops-inline-note">Select a file to inspect it.</div>
                    ) : null}
                  </div>
                </>
              ) : (
                <div className="admin-ops-inline-note">
                  Select a paper to inspect its artifacts.
                </div>
              )}
            </div>
          </div>
        </section>
      </div>
    </section>
  );
}
