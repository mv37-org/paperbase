import { type ReactNode, useEffect, useMemo, useState } from "react";
import type { EChartsOption } from "echarts";
import ReactECharts from "echarts-for-react";

import {
  getBrowserAdminPaperAsset,
  getBrowserAdminPaperFile,
  getBrowserPaper,
  getBrowserPaperPreview,
  loadBrowserAdminJobPapers,
  loadBrowserAdminJobsOverview,
  loadBrowserAdminPaperFiles,
  queueBrowserAdminEnrichMissingMetadata,
  queueBrowserAdminHarvestRange,
  queueBrowserAdminHarvestTopCited,
  queueBrowserAdminHarvestVenue,
  queueBrowserAdminPaperIndex,
} from "./api";
import type {
  AdminJobOverviewRow,
  AdminJobPaperResponse,
  FileContentResponse,
  FileObject,
  PaperAssetResponse,
  PaperPreviewResponse,
  PaperResponse,
} from "./types";

type AdminWorkspaceView = { kind: "jobs" } | { kind: "papers"; rootJobId: string };

type AdminOpsPanelProps = {
  view: AdminWorkspaceView;
  onBackToJobs: () => void;
  onError: (message: string) => void;
  onNote: (message: string) => void;
  onOpenJobPapers: (rootJobId: string) => void;
};

type CreateJobKind =
  | "index_paper"
  | "harvest_range"
  | "harvest_top_cited"
  | "harvest_venue"
  | "enrich_missing_metadata";

type PaperModalTab = "screenshot" | "metadata" | "filestructure";

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

const JOB_TYPE_OPTIONS: Array<{
  kind: CreateJobKind;
  label: string;
  description: string;
  estimate: string;
}> = [
  {
    kind: "index_paper",
    label: "Single paper",
    description:
      "Index one paper from arXiv so it becomes searchable and its artifacts land in storage.",
    estimate: "Usually 1-3 minutes.",
  },
  {
    kind: "harvest_range",
    label: "Date range",
    description:
      "Walk a date window across one or more arXiv categories and index each matching paper in chunks.",
    estimate: "Usually 10 minutes to a few hours, depending on the date span and limits.",
  },
  {
    kind: "harvest_top_cited",
    label: "Top cited",
    description:
      "Ask Semantic Scholar for highly cited papers, then index the arXiv-linked ones in batches.",
    estimate: "Usually 15 minutes to a few hours, depending on the paper cap.",
  },
  {
    kind: "harvest_venue",
    label: "Venue year",
    description:
      "Fetch papers from one conference or journal year, then index the arXiv-linked subset.",
    estimate: "Usually 15 minutes to a few hours, depending on the venue size.",
  },
  {
    kind: "enrich_missing_metadata",
    label: "Metadata enrichment",
    description:
      "Revisit already stored papers and fill in missing Semantic Scholar fields like citations and TLDRs.",
    estimate: "Usually a few minutes to an hour, depending on how many papers need backfill.",
  },
];

const JOB_TYPE_LABELS: Record<string, string> = {
  index_paper: "Single paper",
  harvest_range: "Date range",
  harvest_top_cited: "Top cited",
  harvest_venue: "Venue year",
  enrich_missing_metadata: "Metadata enrichment",
  latest_sync_schedule: "Latest sync",
  sync_latest: "Latest sync",
};

const HOUR_MS = 60 * 60 * 1000;
const DAY_MS = 24 * HOUR_MS;
const TIMELINE_BUCKETS = [
  { maxSpanMs: 2 * DAY_MS, sizeMs: HOUR_MS, label: "Hourly buckets" },
  { maxSpanMs: 14 * DAY_MS, sizeMs: 6 * HOUR_MS, label: "6-hour buckets" },
  { maxSpanMs: 90 * DAY_MS, sizeMs: DAY_MS, label: "Daily buckets" },
  { maxSpanMs: 365 * DAY_MS, sizeMs: 7 * DAY_MS, label: "Weekly buckets" },
];
const LOG_SURFACE_TEXT = "#edf3fb";
const LOG_SURFACE_MUTED = "#90a0b5";
const LOG_SURFACE_LINE = "rgba(144, 160, 181, 0.2)";
const TIMELINE_BAR = "#5f87ff";
const INTEGER_FORMATTER = new Intl.NumberFormat("en-US");

type TimelineBucketPoint = {
  axisLabel: string;
  bucketLabel: string;
  count: number;
  jobCount: number;
};

type TimelineSummary = {
  bucketLabel: string;
  buckets: TimelineBucketPoint[];
  latestUpdatedAt: string | null;
  totalIndexed: number;
  trackedJobs: number;
};

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

function formatWholeNumber(value: number) {
  return INTEGER_FORMATTER.format(value);
}

function statusLabel(value: string) {
  if (!value) {
    return "Unknown";
  }
  if (value === "error") {
    return "Error";
  }
  if (value === "stalled") {
    return "Stalled";
  }
  if (value === "scheduled") {
    return "Scheduled";
  }
  return `${value[0]?.toUpperCase() ?? ""}${value.slice(1)}`;
}

function jobTypeLabel(jobType: string) {
  return JOB_TYPE_LABELS[jobType] ?? jobType.replaceAll("_", " ");
}

function panelErrorMessage(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
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

function selectTimelineBucket(spanMs: number) {
  return TIMELINE_BUCKETS.find((bucket) => spanMs <= bucket.maxSpanMs) ?? TIMELINE_BUCKETS.at(-1)!;
}

function formatTimelineAxisLabel(timestamp: number, bucketMs: number) {
  const date = new Date(timestamp);
  const options: Intl.DateTimeFormatOptions =
    bucketMs < DAY_MS
      ? { month: "short", day: "numeric", hour: "2-digit" }
      : { month: "short", day: "numeric" };
  return date.toLocaleString([], options);
}

function formatTimelineTooltipLabel(timestamp: number, bucketMs: number) {
  const start = new Date(timestamp);
  const end = new Date(timestamp + bucketMs);
  if (bucketMs >= DAY_MS) {
    return `${start.toLocaleDateString()} to ${new Date(end.getTime() - 1).toLocaleDateString()}`;
  }
  return `${start.toLocaleString()} to ${end.toLocaleString()}`;
}

function buildJobsTimeline(jobs: AdminJobOverviewRow[]): TimelineSummary {
  const trackedJobs = jobs
    .filter(
      (job) =>
        job.tracking_available &&
        typeof job.indexed_paper_count === "number" &&
        Number.isFinite(job.indexed_paper_count) &&
        job.updated_at,
    )
    .map((job) => {
      const timestamp = new Date(job.updated_at).getTime();
      return Number.isNaN(timestamp) ? null : { ...job, timestamp };
    })
    .filter((job): job is AdminJobOverviewRow & { timestamp: number } => Boolean(job));

  if (trackedJobs.length === 0) {
    return {
      bucketLabel: "Daily buckets",
      buckets: [],
      latestUpdatedAt: null,
      totalIndexed: 0,
      trackedJobs: 0,
    };
  }

  const minTimestamp = Math.min(...trackedJobs.map((job) => job.timestamp));
  const maxTimestamp = Math.max(...trackedJobs.map((job) => job.timestamp));
  const selectedBucket = selectTimelineBucket(Math.max(maxTimestamp - minTimestamp, 1));
  const startTimestamp = Math.floor(minTimestamp / selectedBucket.sizeMs) * selectedBucket.sizeMs;
  const endTimestamp = Math.floor(maxTimestamp / selectedBucket.sizeMs) * selectedBucket.sizeMs;
  const byBucket = new Map<number, { count: number; jobCount: number }>();

  for (const job of trackedJobs) {
    const bucketTimestamp =
      Math.floor(job.timestamp / selectedBucket.sizeMs) * selectedBucket.sizeMs;
    const current = byBucket.get(bucketTimestamp) ?? { count: 0, jobCount: 0 };
    current.count += Math.max(job.indexed_paper_count ?? 0, 0);
    current.jobCount += 1;
    byBucket.set(bucketTimestamp, current);
  }

  const buckets: TimelineBucketPoint[] = [];
  for (let cursor = startTimestamp; cursor <= endTimestamp; cursor += selectedBucket.sizeMs) {
    const entry = byBucket.get(cursor) ?? { count: 0, jobCount: 0 };
    buckets.push({
      axisLabel: formatTimelineAxisLabel(cursor, selectedBucket.sizeMs),
      bucketLabel: formatTimelineTooltipLabel(cursor, selectedBucket.sizeMs),
      count: entry.count,
      jobCount: entry.jobCount,
    });
  }

  const latestJob = trackedJobs.reduce((current, candidate) =>
    candidate.timestamp > current.timestamp ? candidate : current,
  );

  return {
    bucketLabel: selectedBucket.label,
    buckets,
    latestUpdatedAt: latestJob.updated_at,
    totalIndexed: trackedJobs.reduce(
      (sum, job) => sum + Math.max(job.indexed_paper_count ?? 0, 0),
      0,
    ),
    trackedJobs: trackedJobs.length,
  };
}

function detailRows(job: AdminJobOverviewRow) {
  const details = job.details ?? {};
  if (job.job_type === "latest_sync_schedule") {
    return [
      ["Interval", `${details.sync_interval_seconds ?? 0}s`],
      ["Categories", Array.isArray(details.categories) ? details.categories.join(", ") : "—"],
      ["Days", String(details.days ?? "—")],
      ["Per-category limit", String(details.limit_per_category ?? "—")],
      ["Next expected run", formatTimestamp(String(details.next_expected_run_at ?? ""))],
      [
        "Latest run status",
        details.latest_run_status ? statusLabel(String(details.latest_run_status)) : "No runs yet",
      ],
      ["Latest run updated", formatTimestamp(String(details.latest_run_updated_at ?? ""))],
    ];
  }
  if (job.job_type === "harvest_range") {
    return [
      ["Harvest id", String(details.harvest_id ?? job.root_job_id)],
      ["Categories", Array.isArray(details.categories) ? details.categories.join(", ") : "—"],
      ["Window", `${details.start_date ?? "—"} to ${details.end_date ?? "—"}`],
      ["Chunk months", String(details.chunk_months ?? "—")],
      ["Per-window limit", String(details.limit_per_window ?? "—")],
    ];
  }
  if (job.job_type === "harvest_top_cited") {
    return [
      ["Harvest id", String(details.harvest_id ?? job.root_job_id)],
      ["Minimum citations", String(details.min_citations ?? "—")],
      ["Paper cap", String(details.max_papers ?? "—")],
      ["Chunk size", String(details.chunk_size ?? "—")],
    ];
  }
  if (job.job_type === "harvest_venue") {
    return [
      ["Harvest id", String(details.harvest_id ?? job.root_job_id)],
      ["Venue", String(details.venue ?? "—")],
      ["Year", String(details.year ?? "—")],
      ["Paper cap", String(details.max_papers ?? "—")],
      ["Chunk size", String(details.chunk_size ?? "—")],
    ];
  }
  if (job.job_type === "index_paper") {
    return [
      ["arXiv id", String(details.arxiv_id ?? "—")],
      ["Force reindex", details.force ? "Yes" : "No"],
    ];
  }
  if (job.job_type === "enrich_missing_metadata") {
    return [
      ["Limit", String(details.limit ?? "—")],
      ["Min age (seconds)", String(details.min_age_seconds ?? "—")],
    ];
  }
  return [];
}

function metadataRows(paper: PaperResponse | null, entry: AdminJobPaperResponse | null) {
  if (!paper && !entry) {
    return [];
  }
  return [
    ["Paper id", paper?.paper_id ?? entry?.paper_id ?? "—"],
    ["arXiv id", paper?.arxiv_id ?? entry?.arxiv_id ?? "—"],
    ["Title", paper?.title ?? entry?.title ?? "—"],
    ["Authors", paper?.authors ?? "—"],
    ["Status", paper?.index_status ?? entry?.outcome ?? "—"],
    ["Categories", paper?.categories ?? "—"],
    ["Published", paper?.published ?? "—"],
    ["Updated", paper?.updated ?? "—"],
    ["Citations", paper ? String(paper.citation_count) : "—"],
    ["Source type", paper?.source_type ?? "—"],
  ];
}

function JobsTimeline({ jobs, loading }: { jobs: AdminJobOverviewRow[]; loading: boolean }) {
  const timeline = useMemo(() => buildJobsTimeline(jobs), [jobs]);
  const option = useMemo<EChartsOption>(() => {
    return {
      animation: false,
      backgroundColor: "transparent",
      textStyle: {
        color: LOG_SURFACE_TEXT,
        fontFamily: '"JetBrains Mono", "SFMono-Regular", monospace',
      },
      grid: { top: 18, right: 18, bottom: 56, left: 56 },
      tooltip: {
        trigger: "axis",
        axisPointer: {
          type: "shadow",
          shadowStyle: { color: "rgba(95, 135, 255, 0.12)" },
        },
        backgroundColor: "#0a0f15",
        borderColor: LOG_SURFACE_LINE,
        textStyle: {
          color: LOG_SURFACE_TEXT,
          fontFamily: '"JetBrains Mono", "SFMono-Regular", monospace',
        },
        formatter: (params: unknown) => {
          const point = Array.isArray(params) ? params[0] : params;
          const dataIndex =
            point && typeof point === "object" && "dataIndex" in point
              ? Number((point as { dataIndex?: number }).dataIndex ?? 0)
              : 0;
          const bucket = timeline.buckets[dataIndex];
          if (!bucket) {
            return "";
          }
          return [
            bucket.bucketLabel,
            `${formatWholeNumber(bucket.count)} indexed papers`,
            `${formatWholeNumber(bucket.jobCount)} tracked jobs`,
          ].join("<br/>");
        },
      },
      xAxis: {
        type: "category",
        data: timeline.buckets.map((bucket) => bucket.axisLabel),
        boundaryGap: true,
        axisLine: { lineStyle: { color: LOG_SURFACE_LINE } },
        axisTick: { show: false },
        axisLabel: {
          color: LOG_SURFACE_MUTED,
          hideOverlap: true,
          margin: 12,
        },
      },
      yAxis: {
        type: "value",
        minInterval: 1,
        splitNumber: 4,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: {
          color: LOG_SURFACE_MUTED,
          formatter: (value: number) =>
            value >= 1000 ? `${(value / 1000).toFixed(value >= 10000 ? 0 : 1)}k` : `${value}`,
        },
        splitLine: {
          lineStyle: { color: LOG_SURFACE_LINE },
        },
      },
      dataZoom: [
        {
          type: "inside",
          filterMode: "none",
          zoomLock: false,
        },
        {
          type: "slider",
          height: 20,
          bottom: 16,
          brushSelect: false,
          borderColor: LOG_SURFACE_LINE,
          fillerColor: "rgba(95, 135, 255, 0.16)",
          backgroundColor: "rgba(255, 255, 255, 0.03)",
          handleStyle: {
            color: "#9db6ff",
            borderColor: "#9db6ff",
          },
          moveHandleStyle: { color: "transparent", opacity: 0 },
          textStyle: {
            color: LOG_SURFACE_MUTED,
            fontFamily: '"JetBrains Mono", "SFMono-Regular", monospace',
          },
        },
      ],
      series: [
        {
          name: "Indexed papers",
          type: "bar",
          data: timeline.buckets.map((bucket) => bucket.count),
          barMinHeight: 2,
          barMaxWidth: 20,
          itemStyle: {
            color: TIMELINE_BAR,
            borderRadius: [4, 4, 0, 0],
          },
          emphasis: {
            itemStyle: {
              color: "#7ea0ff",
            },
          },
        },
      ],
    };
  }, [timeline]);

  return (
    <section className="admin-jobs-timeline-panel" aria-label="Indexed papers timeline">
      <div className="admin-jobs-timeline-head">
        <div className="admin-jobs-timeline-copy">
          <span className="admin-jobs-timeline-kicker">Timeline</span>
          <strong>
            {loading
              ? "Loading indexed-paper history"
              : `${formatWholeNumber(timeline.totalIndexed)} indexed papers`}
          </strong>
          <span>
            {loading
              ? "Fetching tracked jobs"
              : `${formatWholeNumber(timeline.trackedJobs)} tracked jobs${timeline.trackedJobs > 0 ? ` · ${timeline.bucketLabel}` : ""}`}
          </span>
        </div>
        {timeline.latestUpdatedAt ? (
          <div className="admin-jobs-timeline-meta">
            Last update
            <strong>{formatTimestamp(timeline.latestUpdatedAt)}</strong>
          </div>
        ) : null}
      </div>

      {loading ? (
        <div className="admin-jobs-timeline-empty">Loading timeline...</div>
      ) : timeline.buckets.length > 0 ? (
        <div className="admin-jobs-chart-shell" data-testid="admin-jobs-chart">
          <ReactECharts option={option} className="admin-jobs-chart" notMerge lazyUpdate />
        </div>
      ) : (
        <div className="admin-jobs-timeline-empty">No indexed-paper history yet.</div>
      )}
    </section>
  );
}

export function AdminOpsPanel({
  view,
  onBackToJobs,
  onError,
  onNote,
  onOpenJobPapers,
}: AdminOpsPanelProps) {
  const [jobs, setJobs] = useState<AdminJobOverviewRow[]>([]);
  const [jobsLoading, setJobsLoading] = useState(false);
  const [jobsError, setJobsError] = useState("");
  const [expandedJobIds, setExpandedJobIds] = useState<Record<string, boolean>>({});

  const [jobPapers, setJobPapers] = useState<AdminJobPaperResponse[]>([]);
  const [jobPapersLoading, setJobPapersLoading] = useState(false);
  const [jobPapersError, setJobPapersError] = useState("");

  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [createJobKind, setCreateJobKind] = useState<CreateJobKind | null>(null);
  const [createSubmitting, setCreateSubmitting] = useState(false);

  const [selectedJobPaper, setSelectedJobPaper] = useState<AdminJobPaperResponse | null>(null);
  const [paperModalTab, setPaperModalTab] = useState<PaperModalTab>("screenshot");
  const [paperDetail, setPaperDetail] = useState<PaperResponse | null>(null);
  const [paperPreview, setPaperPreview] = useState<PaperPreviewResponse | null>(null);
  const [paperModalLoading, setPaperModalLoading] = useState(false);
  const [paperModalError, setPaperModalError] = useState("");

  const [paperFiles, setPaperFiles] = useState<FileObject[]>([]);
  const [paperFilesLoading, setPaperFilesLoading] = useState(false);
  const [paperFilesError, setPaperFilesError] = useState("");
  const [selectedFilePath, setSelectedFilePath] = useState<string | null>(null);
  const [selectedFileContent, setSelectedFileContent] = useState<FileContentResponse | null>(null);
  const [selectedAsset, setSelectedAsset] = useState<PaperAssetResponse | null>(null);
  const [selectedFileLoading, setSelectedFileLoading] = useState(false);
  const [selectedFileError, setSelectedFileError] = useState("");

  const activeCreateJob = useMemo(
    () => JOB_TYPE_OPTIONS.find((option) => option.kind === createJobKind) ?? null,
    [createJobKind],
  );

  async function refreshJobs() {
    setJobsLoading(true);
    setJobsError("");
    try {
      const response = await loadBrowserAdminJobsOverview(75);
      setJobs(response.jobs);
    } catch (error) {
      setJobsError(panelErrorMessage(error, "Jobs could not be loaded."));
    } finally {
      setJobsLoading(false);
    }
  }

  async function refreshJobPapers(rootJobId: string) {
    setJobPapersLoading(true);
    setJobPapersError("");
    try {
      const response = await loadBrowserAdminJobPapers(rootJobId);
      setJobPapers(response.papers);
    } catch (error) {
      setJobPapersError(panelErrorMessage(error, "Indexed papers could not be loaded."));
    } finally {
      setJobPapersLoading(false);
    }
  }

  useEffect(() => {
    if (view.kind === "jobs") {
      void refreshJobs();
    }
  }, [view.kind]);

  useEffect(() => {
    if (view.kind === "papers") {
      void refreshJobPapers(view.rootJobId);
    }
  }, [view.kind, view.kind === "papers" ? view.rootJobId : null]);

  useEffect(() => {
    if (!selectedJobPaper) {
      setPaperModalTab("screenshot");
      setPaperDetail(null);
      setPaperPreview(null);
      setPaperModalLoading(false);
      setPaperModalError("");
      setPaperFiles([]);
      setPaperFilesLoading(false);
      setPaperFilesError("");
      setSelectedFilePath(null);
      setSelectedFileContent(null);
      setSelectedAsset(null);
      setSelectedFileLoading(false);
      setSelectedFileError("");
      return;
    }

    let cancelled = false;
    setPaperModalLoading(true);
    setPaperModalError("");
    setPaperFiles([]);
    setPaperFilesError("");
    setSelectedFilePath(null);
    setSelectedFileContent(null);
    setSelectedAsset(null);
    setSelectedFileError("");

    void (async () => {
      try {
        const [paper, preview] = await Promise.all([
          getBrowserPaper(selectedJobPaper.paper_id).catch(() => selectedJobPaper.paper ?? null),
          getBrowserPaperPreview(selectedJobPaper.paper_id).catch(() => null),
        ]);
        if (cancelled) {
          return;
        }
        setPaperDetail(paper);
        setPaperPreview(preview);
      } catch (error) {
        if (cancelled) {
          return;
        }
        setPaperModalError(panelErrorMessage(error, "Paper details could not be loaded."));
      } finally {
        if (!cancelled) {
          setPaperModalLoading(false);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [selectedJobPaper]);

  useEffect(() => {
    if (!selectedJobPaper || paperModalTab !== "filestructure") {
      return;
    }
    let cancelled = false;
    setPaperFilesLoading(true);
    setPaperFilesError("");
    void (async () => {
      try {
        const response = await loadBrowserAdminPaperFiles(selectedJobPaper.paper_id);
        if (cancelled) {
          return;
        }
        setPaperFiles(response.files);
      } catch (error) {
        if (cancelled) {
          return;
        }
        setPaperFilesError(panelErrorMessage(error, "Paper files could not be loaded."));
      } finally {
        if (!cancelled) {
          setPaperFilesLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [paperModalTab, selectedJobPaper]);

  async function openFile(file: FileObject) {
    if (!selectedJobPaper) {
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
          const response = await getBrowserAdminPaperFile(selectedJobPaper.paper_id, file.path);
          setSelectedFileContent(response);
          return;
        } catch (error) {
          const message = panelErrorMessage(error, "");
          if (!message.toLowerCase().includes("not a utf-8 text artifact")) {
            throw error;
          }
        }
      }
      const asset = await getBrowserAdminPaperAsset(selectedJobPaper.paper_id, file.path);
      setSelectedAsset(asset);
    } catch (error) {
      const message = panelErrorMessage(error, "Paper file could not be opened.");
      setSelectedFileError(message);
      onError(message);
    } finally {
      setSelectedFileLoading(false);
    }
  }

  async function submitCreateJob(formData: FormData) {
    if (!createJobKind) {
      return;
    }
    setCreateSubmitting(true);
    try {
      if (createJobKind === "index_paper") {
        const arxivId = String(formData.get("arxiv_id") ?? "").trim();
        if (!arxivId) {
          throw new Error("Enter an arXiv id.");
        }
        await queueBrowserAdminPaperIndex(arxivId, formData.get("force") === "on");
      } else if (createJobKind === "harvest_range") {
        await queueBrowserAdminHarvestRange({
          categories: parseCsvList(String(formData.get("categories") ?? "")),
          start_date: String(formData.get("start_date") ?? "").trim(),
          end_date: String(formData.get("end_date") ?? "").trim(),
          limit_per_window: parseOptionalInteger(formData.get("limit_per_window")),
          chunk_months: Number.parseInt(String(formData.get("chunk_months") ?? "3"), 10),
          source: "arxiv",
        });
      } else if (createJobKind === "harvest_top_cited") {
        await queueBrowserAdminHarvestTopCited({
          min_citations: Number.parseInt(String(formData.get("min_citations") ?? "100"), 10),
          max_papers: parseOptionalInteger(formData.get("max_papers")),
          chunk_size: Number.parseInt(String(formData.get("chunk_size") ?? "50"), 10),
        });
      } else if (createJobKind === "harvest_venue") {
        await queueBrowserAdminHarvestVenue({
          venue: String(formData.get("venue") ?? "").trim(),
          year: Number.parseInt(String(formData.get("year") ?? "2024"), 10),
          max_papers: parseOptionalInteger(formData.get("max_papers")),
          chunk_size: Number.parseInt(String(formData.get("chunk_size") ?? "50"), 10),
        });
      } else {
        await queueBrowserAdminEnrichMissingMetadata(
          Number.parseInt(String(formData.get("limit") ?? "50000"), 10),
          Number.parseInt(String(formData.get("min_age_seconds") ?? "3600"), 10),
        );
      }
      onNote(`Queued ${activeCreateJob?.label ?? "job"}.`);
      setCreateModalOpen(false);
      setCreateJobKind(null);
      await refreshJobs();
    } catch (error) {
      onError(panelErrorMessage(error, "Job could not be queued."));
    } finally {
      setCreateSubmitting(false);
    }
  }

  return (
    <section className="admin-jobs-shell">
      {view.kind === "jobs" ? (
        <>
          <div className="admin-jobs-header">
            <h1>Jobs</h1>
            <button
              type="button"
              className="admin-compact-action admin-compact-action-primary admin-compact-action-with-icon admin-jobs-create-button"
              onClick={() => {
                setCreateModalOpen(true);
                setCreateJobKind(null);
              }}
            >
              <span className="admin-compact-action-icon" aria-hidden="true">
                +
              </span>
              <span className="admin-compact-action-label">Create job</span>
            </button>
          </div>

          {jobsError ? <div className="admin-jobs-inline-error">{jobsError}</div> : null}
          <div className="admin-jobs-surface">
            <JobsTimeline jobs={jobs} loading={jobsLoading} />
            <div className="admin-jobs-results-bar">
              {jobsLoading ? "Loading jobs" : `${formatWholeNumber(jobs.length)} jobs`}
            </div>
            {jobsLoading ? <div className="admin-jobs-empty">Loading jobs...</div> : null}
            {!jobsLoading ? (
              <div className="admin-jobs-table-shell">
                <table className="admin-jobs-table">
                  <thead>
                    <tr>
                      <th>Type</th>
                      <th>ID</th>
                      <th>Status</th>
                      <th>Indexed papers</th>
                      <th>Updated</th>
                    </tr>
                  </thead>
                  <tbody>
                    {jobs.length === 0 ? (
                      <tr className="admin-jobs-empty-row">
                        <td colSpan={5}>
                          <div className="admin-jobs-empty">No jobs yet.</div>
                        </td>
                      </tr>
                    ) : (
                      jobs.map((job) => {
                        const expanded = Boolean(expandedJobIds[job.job_id]);
                        const detailPairs = detailRows(job);
                        return (
                          <FragmentRow key={job.job_id}>
                            <tr className="admin-jobs-row">
                              <td>
                                <button
                                  type="button"
                                  className="admin-jobs-expand"
                                  onClick={() =>
                                    setExpandedJobIds((current) => ({
                                      ...current,
                                      [job.job_id]: !current[job.job_id],
                                    }))
                                  }
                                  aria-expanded={expanded}
                                >
                                  <span>{expanded ? "−" : "+"}</span>
                                  <span>{jobTypeLabel(job.job_type)}</span>
                                </button>
                              </td>
                              <td>
                                <code>{job.job_id}</code>
                              </td>
                              <td>
                                <span className={`admin-jobs-status status-${job.display_status}`}>
                                  {statusLabel(job.display_status)}
                                </span>
                              </td>
                              <td>
                                {job.tracking_available ? (
                                  <button
                                    type="button"
                                    className="admin-jobs-link-button"
                                    onClick={() => onOpenJobPapers(job.root_job_id)}
                                  >
                                    {job.indexed_paper_count ?? 0}
                                  </button>
                                ) : (
                                  <span>—</span>
                                )}
                              </td>
                              <td>{formatTimestamp(job.updated_at)}</td>
                            </tr>
                            {expanded ? (
                              <tr className="admin-jobs-expanded-row">
                                <td colSpan={5}>
                                  <div className="admin-jobs-expanded">
                                    <div>Latest status: {job.latest_status_summary}</div>
                                    {job.error ? <div>Error: {job.error}</div> : null}
                                    {detailPairs.map(([label, value]) => (
                                      <div key={label}>
                                        {label}: {value}
                                      </div>
                                    ))}
                                    {job.child_job_count > 0 ? (
                                      <div>Sub-jobs: {job.child_job_count}</div>
                                    ) : null}
                                  </div>
                                </td>
                              </tr>
                            ) : null}
                          </FragmentRow>
                        );
                      })
                    )}
                  </tbody>
                </table>
              </div>
            ) : null}
          </div>
        </>
      ) : (
        <>
          <div className="admin-jobs-header admin-jobs-header-papers">
            <button type="button" className="secondary-button" onClick={onBackToJobs}>
              Back to jobs
            </button>
            <h1>Indexed papers</h1>
          </div>

          {jobPapersError ? <div className="admin-jobs-inline-error">{jobPapersError}</div> : null}
          <div className="admin-jobs-surface">
            <div className="admin-jobs-results-bar">
              {jobPapersLoading
                ? "Loading papers"
                : `${formatWholeNumber(jobPapers.length)} papers`}
            </div>
            {jobPapersLoading ? <div className="admin-jobs-empty">Loading papers...</div> : null}
            {!jobPapersLoading ? (
              <div className="admin-jobs-table-shell">
                <table className="admin-jobs-table admin-papers-table">
                  <thead>
                    <tr>
                      <th>Paper</th>
                      <th>Outcome</th>
                      <th>Updated</th>
                    </tr>
                  </thead>
                  <tbody>
                    {jobPapers.length === 0 ? (
                      <tr className="admin-jobs-empty-row">
                        <td colSpan={3}>
                          <div className="admin-jobs-empty">
                            No tracked papers for this job yet.
                          </div>
                        </td>
                      </tr>
                    ) : (
                      jobPapers.map((paper) => (
                        <tr
                          key={`${paper.job_id}:${paper.paper_id}`}
                          className="admin-jobs-row admin-paper-row"
                          onClick={() => setSelectedJobPaper(paper)}
                        >
                          <td>
                            <div className="admin-paper-primary">
                              {paper.title ?? paper.paper_id}
                            </div>
                            <div className="admin-paper-secondary">
                              {paper.arxiv_id ?? paper.paper_id}
                            </div>
                          </td>
                          <td>
                            <span className={`admin-jobs-status status-${paper.outcome}`}>
                              {statusLabel(paper.outcome)}
                            </span>
                          </td>
                          <td>{formatTimestamp(paper.updated_at)}</td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            ) : null}
          </div>
        </>
      )}

      {createModalOpen ? (
        <div
          className="modal"
          role="dialog"
          aria-modal="true"
          aria-labelledby="admin-create-job-title"
        >
          <div
            className="modal-backdrop"
            onClick={() => {
              if (createSubmitting) {
                return;
              }
              setCreateModalOpen(false);
              setCreateJobKind(null);
            }}
          />
          <div className="modal-panel admin-create-job-modal">
            <button
              type="button"
              className="modal-close"
              aria-label="Close create job"
              onClick={() => {
                if (createSubmitting) {
                  return;
                }
                setCreateModalOpen(false);
                setCreateJobKind(null);
              }}
            >
              ×
            </button>
            <h2 id="admin-create-job-title">Create job</h2>
            {!activeCreateJob ? (
              <div className="admin-create-job-chooser">
                {JOB_TYPE_OPTIONS.map((option) => (
                  <button
                    key={option.kind}
                    type="button"
                    className="admin-create-job-option"
                    onClick={() => setCreateJobKind(option.kind)}
                  >
                    <span className="admin-create-job-option-copy">
                      <strong>{option.label}</strong>
                      <span>{option.description}</span>
                      <em>{option.estimate}</em>
                    </span>
                    <span className="admin-create-job-option-arrow" aria-hidden="true">
                      →
                    </span>
                  </button>
                ))}
              </div>
            ) : (
              <form
                className="admin-create-job-form"
                onSubmit={async (event) => {
                  event.preventDefault();
                  await submitCreateJob(new FormData(event.currentTarget));
                }}
              >
                <div className="admin-create-job-summary">
                  <strong>{activeCreateJob.label}</strong>
                  <span>{activeCreateJob.description}</span>
                  <em>{activeCreateJob.estimate}</em>
                </div>

                {createJobKind === "index_paper" ? (
                  <>
                    <label className="settings-field">
                      <span className="settings-field-label">arXiv ID</span>
                      <input name="arxiv_id" placeholder="2401.01234" />
                    </label>
                    <label className="admin-create-job-checkbox">
                      <input name="force" type="checkbox" />
                      <span>Force reindex</span>
                    </label>
                  </>
                ) : null}

                {createJobKind === "harvest_range" ? (
                  <>
                    <label className="settings-field">
                      <span className="settings-field-label">Categories</span>
                      <textarea name="categories" rows={3} placeholder="cs.LG, cs.CL" />
                    </label>
                    <div className="admin-create-job-grid">
                      <label className="settings-field">
                        <span className="settings-field-label">Start date</span>
                        <input name="start_date" placeholder="2024-01-01" />
                      </label>
                      <label className="settings-field">
                        <span className="settings-field-label">End date</span>
                        <input name="end_date" placeholder="2024-03-31" />
                      </label>
                    </div>
                    <div className="admin-create-job-grid">
                      <label className="settings-field">
                        <span className="settings-field-label">Per-window limit</span>
                        <input name="limit_per_window" placeholder="1000" />
                      </label>
                      <label className="settings-field">
                        <span className="settings-field-label">Chunk months</span>
                        <input name="chunk_months" defaultValue="3" />
                      </label>
                    </div>
                  </>
                ) : null}

                {createJobKind === "harvest_top_cited" ? (
                  <>
                    <div className="admin-create-job-grid">
                      <label className="settings-field">
                        <span className="settings-field-label">Minimum citations</span>
                        <input name="min_citations" defaultValue="100" />
                      </label>
                      <label className="settings-field">
                        <span className="settings-field-label">Chunk size</span>
                        <input name="chunk_size" defaultValue="50" />
                      </label>
                    </div>
                    <label className="settings-field">
                      <span className="settings-field-label">Max papers</span>
                      <input name="max_papers" placeholder="Optional" />
                    </label>
                  </>
                ) : null}

                {createJobKind === "harvest_venue" ? (
                  <>
                    <div className="admin-create-job-grid">
                      <label className="settings-field">
                        <span className="settings-field-label">Venue</span>
                        <input name="venue" placeholder="ICLR" />
                      </label>
                      <label className="settings-field">
                        <span className="settings-field-label">Year</span>
                        <input name="year" defaultValue="2024" />
                      </label>
                    </div>
                    <div className="admin-create-job-grid">
                      <label className="settings-field">
                        <span className="settings-field-label">Max papers</span>
                        <input name="max_papers" placeholder="Optional" />
                      </label>
                      <label className="settings-field">
                        <span className="settings-field-label">Chunk size</span>
                        <input name="chunk_size" defaultValue="50" />
                      </label>
                    </div>
                  </>
                ) : null}

                {createJobKind === "enrich_missing_metadata" ? (
                  <>
                    <div className="admin-create-job-grid">
                      <label className="settings-field">
                        <span className="settings-field-label">Limit</span>
                        <input name="limit" defaultValue="50000" />
                      </label>
                      <label className="settings-field">
                        <span className="settings-field-label">Min age (seconds)</span>
                        <input name="min_age_seconds" defaultValue="3600" />
                      </label>
                    </div>
                  </>
                ) : null}

                <div className="admin-create-job-actions">
                  <button
                    type="button"
                    className="admin-compact-action admin-compact-action-secondary"
                    onClick={() => setCreateJobKind(null)}
                    disabled={createSubmitting}
                  >
                    Change type
                  </button>
                  <button
                    type="submit"
                    className="admin-compact-action admin-compact-action-primary"
                    disabled={createSubmitting}
                  >
                    {createSubmitting ? "Queuing..." : "Queue job"}
                  </button>
                </div>
              </form>
            )}
          </div>
        </div>
      ) : null}

      {selectedJobPaper ? (
        <div className="modal" role="dialog" aria-modal="true" aria-labelledby="admin-paper-title">
          <div className="modal-backdrop" onClick={() => setSelectedJobPaper(null)} />
          <div className="modal-panel admin-paper-modal">
            <button
              type="button"
              className="modal-close"
              aria-label="Close paper details"
              onClick={() => setSelectedJobPaper(null)}
            >
              ×
            </button>
            <div className="admin-paper-modal-header">
              <h2 id="admin-paper-title">
                {paperDetail?.title ?? selectedJobPaper.title ?? selectedJobPaper.paper_id}
              </h2>
              <span>{selectedJobPaper.arxiv_id ?? selectedJobPaper.paper_id}</span>
            </div>
            <div className="admin-paper-modal-tabs" role="tablist" aria-label="Paper detail views">
              {(["screenshot", "metadata", "filestructure"] as PaperModalTab[]).map((tab) => (
                <button
                  key={tab}
                  type="button"
                  role="tab"
                  aria-selected={paperModalTab === tab}
                  className={`admin-paper-tab${paperModalTab === tab ? " active" : ""}`}
                  onClick={() => setPaperModalTab(tab)}
                >
                  {tab === "screenshot"
                    ? "Screenshot"
                    : tab === "metadata"
                      ? "Metadata"
                      : "Filestructure"}
                </button>
              ))}
            </div>

            {paperModalError ? (
              <div className="admin-jobs-inline-error">{paperModalError}</div>
            ) : null}
            {paperModalLoading ? <div className="admin-jobs-empty">Loading paper...</div> : null}

            {!paperModalLoading && paperModalTab === "screenshot" ? (
              <div className="admin-paper-modal-body">
                {paperPreview ? (
                  <img
                    className="admin-paper-preview"
                    src={paperPreview.image_url}
                    alt={paperDetail?.title ?? selectedJobPaper.paper_id}
                  />
                ) : (
                  <div className="admin-jobs-empty">No screenshot available for this paper.</div>
                )}
              </div>
            ) : null}

            {!paperModalLoading && paperModalTab === "metadata" ? (
              <div className="admin-paper-modal-body admin-paper-metadata">
                {metadataRows(paperDetail, selectedJobPaper).map(([label, value]) => (
                  <div key={label} className="admin-paper-metadata-row">
                    <span>{label}</span>
                    <strong>{value}</strong>
                  </div>
                ))}
              </div>
            ) : null}

            {!paperModalLoading && paperModalTab === "filestructure" ? (
              <div className="admin-paper-modal-body admin-paper-files">
                <div className="admin-paper-file-list">
                  {paperFilesError ? (
                    <div className="admin-jobs-inline-error">{paperFilesError}</div>
                  ) : null}
                  {paperFilesLoading ? (
                    <div className="admin-jobs-empty">Loading files...</div>
                  ) : null}
                  {!paperFilesLoading && paperFiles.length === 0 ? (
                    <div className="admin-jobs-empty">No files found for this paper.</div>
                  ) : null}
                  {!paperFilesLoading
                    ? paperFiles.map((file) => (
                        <button
                          key={file.path}
                          type="button"
                          className={`admin-paper-file-row${selectedFilePath === file.path ? " active" : ""}`}
                          onClick={() => void openFile(file)}
                        >
                          <span>{file.path}</span>
                          <span>{file.content_type ?? "file"}</span>
                        </button>
                      ))
                    : null}
                </div>
                <div className="admin-paper-file-preview">
                  {selectedFileError ? (
                    <div className="admin-jobs-inline-error">{selectedFileError}</div>
                  ) : null}
                  {selectedFileLoading ? (
                    <div className="admin-jobs-empty">Opening file...</div>
                  ) : null}
                  {!selectedFileLoading && selectedFileContent ? (
                    <pre className="admin-paper-file-text">{selectedFileContent.content}</pre>
                  ) : null}
                  {!selectedFileLoading && selectedAsset && assetKind(selectedAsset) === "image" ? (
                    <img
                      className="admin-paper-preview"
                      src={selectedAsset.url}
                      alt={selectedAsset.path}
                    />
                  ) : null}
                  {!selectedFileLoading && selectedAsset && assetKind(selectedAsset) === "pdf" ? (
                    <iframe
                      className="admin-paper-pdf-frame"
                      src={selectedAsset.url}
                      title={selectedAsset.path}
                    />
                  ) : null}
                  {!selectedFileLoading && selectedAsset && assetKind(selectedAsset) === "file" ? (
                    <a
                      href={selectedAsset.url}
                      target="_blank"
                      rel="noreferrer"
                      className="admin-paper-file-link"
                    >
                      Open file
                    </a>
                  ) : null}
                  {!selectedFileLoading &&
                  !selectedFileContent &&
                  !selectedAsset &&
                  !selectedFileError &&
                  selectedJobPaper ? (
                    <div className="admin-jobs-empty">Pick a file to inspect.</div>
                  ) : null}
                </div>
              </div>
            ) : null}
          </div>
        </div>
      ) : null}
    </section>
  );
}

function FragmentRow({ children }: { children: ReactNode }) {
  return <>{children}</>;
}
