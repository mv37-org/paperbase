import {
  useCallback,
  type ComponentPropsWithoutRef,
  type ReactNode,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
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
const ADMIN_JOBS_OVERVIEW_LIMIT = 200;
const CUSTOM_TIME_RANGE_ID = "custom";
const TIMELINE_BUCKETS = [
  { maxSpanMs: 2 * DAY_MS, sizeMs: 45 * 60 * 1000, label: "45-minute buckets" },
  { maxSpanMs: 14 * DAY_MS, sizeMs: 5 * HOUR_MS, label: "5-hour buckets" },
  { maxSpanMs: 90 * DAY_MS, sizeMs: 20 * HOUR_MS, label: "20-hour buckets" },
  { maxSpanMs: 365 * DAY_MS, sizeMs: 6 * DAY_MS, label: "6-day buckets" },
];
const INTEGER_FORMATTER = new Intl.NumberFormat("en-US");

type JobsTimeRangeId =
  | "1h"
  | "6h"
  | "12h"
  | "24h"
  | "3d"
  | "7d"
  | "14d"
  | typeof CUSTOM_TIME_RANGE_ID;

type JobsTimeRangePreset = {
  durationMs: number;
  id: Exclude<JobsTimeRangeId, typeof CUSTOM_TIME_RANGE_ID>;
  label: string;
};

type JobsRefreshIntervalId = "5s" | "30s" | "1m";

type JobsRefreshIntervalOption = {
  id: JobsRefreshIntervalId;
  intervalMs: number;
  label: string;
};

type JobsTimeWindow = {
  endMs: number;
  label: string;
  startMs: number;
};

type AdminJobsTheme = {
  accent: string;
  areaFill: string;
  axisText: string;
  barFill: string;
  line: string;
  text: string;
  tooltipBackground: string;
};

const JOBS_TIME_RANGE_PRESETS: JobsTimeRangePreset[] = [
  { id: "1h", label: "Last hour", durationMs: HOUR_MS },
  { id: "6h", label: "Last 6 hours", durationMs: 6 * HOUR_MS },
  { id: "12h", label: "Last 12 hours", durationMs: 12 * HOUR_MS },
  { id: "24h", label: "Last 24 hours", durationMs: DAY_MS },
  { id: "3d", label: "Last 3 days", durationMs: 3 * DAY_MS },
  { id: "7d", label: "Last 7 days", durationMs: 7 * DAY_MS },
  { id: "14d", label: "Last 2 weeks", durationMs: 14 * DAY_MS },
];

const JOBS_REFRESH_INTERVAL_OPTIONS: JobsRefreshIntervalOption[] = [
  { id: "5s", label: "5 seconds", intervalMs: 5_000 },
  { id: "30s", label: "30 seconds", intervalMs: 30_000 },
  { id: "1m", label: "1 min", intervalMs: 60_000 },
];

const TIMELINE_SKELETON_BAR_HEIGHTS = Array.from({ length: 106 }, (_, index) => {
  const pattern = [0.1, 0.28, 0.18, 0.42, 0.24, 0.52, 0.3, 0.46];
  const step = Math.floor(index / pattern.length) % 3;
  return Math.min(pattern[index % pattern.length] + step * 0.05, 0.7);
});

type TimelineOutcomeKey = "indexed" | "skipped" | "failed";

type TimelineOutcomeCounts = Record<TimelineOutcomeKey, number>;

const TIMELINE_OUTCOMES: Array<{ key: TimelineOutcomeKey; label: string; color: string }> = [
  { key: "indexed", label: "Indexed", color: "#432818" },
  { key: "skipped", label: "Skipped", color: "#99582a" },
  { key: "failed", label: "Failed", color: "#b42318" },
];

type TimelineBucketPoint = {
  axisLabel: string;
  bucketLabel: string;
  jobCount: number;
  outcomes: TimelineOutcomeCounts;
  totalCount: number;
};

type TimelineSummary = {
  bucketLabel: string;
  buckets: TimelineBucketPoint[];
  latestUpdatedAt: string | null;
  maxTotal: number;
  totalIndexed: number;
  trackedJobs: number;
};

type CustomTimeRangeState = {
  end: string;
  start: string;
};

type CreateJobFieldDefinition = {
  label: string;
  help: string;
  required: boolean;
  defaultDescription?: string;
};

const CREATE_JOB_FIELD_DEFINITIONS: Record<
  CreateJobKind,
  Record<string, CreateJobFieldDefinition>
> = {
  index_paper: {
    arxiv_id: {
      label: "arXiv ID",
      help: "The identifier for the single paper you want the worker to fetch and index.",
      required: true,
    },
    force: {
      label: "Force reindex",
      help: "Re-run the indexing pipeline even if this paper already exists in storage.",
      required: false,
      defaultDescription: "Off, so existing papers are left alone",
    },
  },
  harvest_range: {
    categories: {
      label: "Categories",
      help: "The arXiv subjects to scan, like cs.LG or cs.CL, so the backfill stays in the area you care about.",
      required: false,
      defaultDescription: "The server's configured AI/ML arXiv categories",
    },
    start_date: {
      label: "Start date",
      help: "The first calendar day the harvest is allowed to include.",
      required: true,
    },
    end_date: {
      label: "End date",
      help: "The last calendar day the harvest is allowed to include.",
      required: true,
    },
    limit_per_window: {
      label: "Per-window limit",
      help: "A safety cap for each category inside each time chunk, so one busy window does not flood the queue.",
      required: false,
      defaultDescription: "No per-window cap",
    },
    chunk_months: {
      label: "Chunk months",
      help: "How many months of time to bundle together before the planner splits the work into sub-jobs.",
      required: false,
      defaultDescription: "3 months",
    },
  },
  harvest_top_cited: {
    min_citations: {
      label: "Minimum citations",
      help: "The citation floor a paper must clear on Semantic Scholar before we bother trying to index it.",
      required: false,
      defaultDescription: "100 citations",
    },
    max_papers: {
      label: "Max papers",
      help: "A hard stop for how many candidate papers the planner can emit before it quits.",
      required: false,
      defaultDescription: "No cap",
    },
    chunk_size: {
      label: "Chunk size",
      help: "How many arXiv ids to bundle into each worker batch once the planner finds candidates.",
      required: false,
      defaultDescription: "50 papers per batch",
    },
  },
  harvest_venue: {
    venue: {
      label: "Venue",
      help: "The conference or journal name Semantic Scholar should search inside.",
      required: true,
    },
    year: {
      label: "Year",
      help: "The single publication year to search within that venue.",
      required: true,
    },
    max_papers: {
      label: "Max papers",
      help: "A hard stop for how many venue matches the planner can emit before it quits.",
      required: false,
      defaultDescription: "No cap",
    },
    chunk_size: {
      label: "Chunk size",
      help: "How many arXiv ids to bundle into each worker batch after the planner finds venue matches.",
      required: false,
      defaultDescription: "50 papers per batch",
    },
  },
  enrich_missing_metadata: {
    limit: {
      label: "Limit",
      help: "How many already-stored papers this backfill pass is allowed to inspect.",
      required: false,
      defaultDescription: "50,000 papers",
    },
    min_age_seconds: {
      label: "Min age (seconds)",
      help: "How old a stored paper must be before we retry enrichment, so we do not immediately recheck fresh ingests.",
      required: false,
      defaultDescription: "3600 seconds (1 hour)",
    },
  },
};

const DATE_PATTERN = "\\d{4}-\\d{2}-\\d{2}";

function defaultCustomTimeRange(nowMs = Date.now()): CustomTimeRangeState {
  const end = new Date(nowMs);
  const start = new Date(nowMs - DAY_MS);
  return {
    end: formatDateTimeLocalValue(end),
    start: formatDateTimeLocalValue(start),
  };
}

function formatDateTimeLocalValue(date: Date) {
  const shifted = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return shifted.toISOString().slice(0, 16);
}

function parseDateTimeLocalValue(value: string) {
  const text = value.trim();
  if (!text) {
    return null;
  }
  const parsed = new Date(text);
  return Number.isNaN(parsed.getTime()) ? null : parsed.getTime();
}

function formatTimeWindowLabel(startMs: number, endMs: number) {
  const start = new Date(startMs);
  const end = new Date(endMs);
  const sameDay = start.toDateString() === end.toDateString();
  const startOptions: Intl.DateTimeFormatOptions = sameDay
    ? { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }
    : { month: "short", day: "numeric", hour: "numeric" };
  const endOptions: Intl.DateTimeFormatOptions = sameDay
    ? { hour: "numeric", minute: "2-digit" }
    : { month: "short", day: "numeric", hour: "numeric" };
  return `${start.toLocaleString([], startOptions)} to ${end.toLocaleString([], endOptions)}`;
}

function presetTimeWindow(
  presetId: Exclude<JobsTimeRangeId, typeof CUSTOM_TIME_RANGE_ID>,
  nowMs = Date.now(),
): JobsTimeWindow {
  const preset =
    JOBS_TIME_RANGE_PRESETS.find((option) => option.id === presetId) ?? JOBS_TIME_RANGE_PRESETS[5]!;
  return {
    endMs: nowMs,
    label: preset.label,
    startMs: nowMs - preset.durationMs,
  };
}

function customTimeWindow(range: CustomTimeRangeState, nowMs = Date.now()): JobsTimeWindow {
  const fallback = defaultCustomTimeRange(nowMs);
  const startMs = parseDateTimeLocalValue(range.start) ?? parseDateTimeLocalValue(fallback.start)!;
  const endMs = parseDateTimeLocalValue(range.end) ?? parseDateTimeLocalValue(fallback.end)!;
  const normalizedStartMs = Math.min(startMs, endMs);
  const normalizedEndMs = Math.max(startMs, endMs);
  return {
    endMs: normalizedEndMs,
    label: formatTimeWindowLabel(normalizedStartMs, normalizedEndMs),
    startMs: normalizedStartMs,
  };
}

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

function parseIntegerWithDefault(value: FormDataEntryValue | null, defaultValue: number) {
  const text = String(value ?? "").trim();
  if (!text) {
    return defaultValue;
  }
  const parsed = Number.parseInt(text, 10);
  return Number.isNaN(parsed) ? defaultValue : parsed;
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

function adminJobRunReason(job: AdminJobOverviewRow) {
  if (
    job.is_synthetic ||
    job.job_type === "latest_sync_schedule" ||
    job.job_type === "sync_latest"
  ) {
    return "Env scheduled · latest sync";
  }
  if (job.job_type === "index_paper") {
    return "User queued · single paper";
  }
  if (job.job_type === "harvest_range") {
    return "User queued · date range backfill";
  }
  if (job.job_type === "harvest_top_cited") {
    return "User queued · top cited backfill";
  }
  if (job.job_type === "harvest_venue") {
    return "User queued · venue backfill";
  }
  if (job.job_type === "enrich_missing_metadata") {
    return "User queued · metadata enrichment";
  }
  return "User queued · manual job";
}

function panelErrorMessage(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}

function createJobFieldDefinition(jobKind: CreateJobKind, fieldName: string) {
  const definition = CREATE_JOB_FIELD_DEFINITIONS[jobKind][fieldName];
  if (!definition) {
    throw new Error(`Missing create-job field definition for ${jobKind}.${fieldName}`);
  }
  return definition;
}

function createJobFieldId(jobKind: CreateJobKind, fieldName: string) {
  return `create-job-${jobKind}-${fieldName}`;
}

function createJobFieldHelpText(definition: CreateJobFieldDefinition) {
  if (definition.required) {
    return `${definition.help} Required.`;
  }
  return `${definition.help} Optional. Default: ${definition.defaultDescription ?? "none"}.`;
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

function jobsInTimeWindow(jobs: AdminJobOverviewRow[], timeWindow: JobsTimeWindow) {
  return jobs.filter((job) => {
    const timestamp = new Date(job.updated_at).getTime();
    return (
      !Number.isNaN(timestamp) && timestamp >= timeWindow.startMs && timestamp <= timeWindow.endMs
    );
  });
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

function emptyTimelineOutcomeCounts(): TimelineOutcomeCounts {
  return { indexed: 0, skipped: 0, failed: 0 };
}

function timelineOutcomeCountsForJob(job: AdminJobOverviewRow): TimelineOutcomeCounts {
  const counts = emptyTimelineOutcomeCounts();
  const rawCounts = job.paper_outcome_counts ?? {};
  let hasOutcomeCounts = false;
  for (const { key } of TIMELINE_OUTCOMES) {
    const value = rawCounts[key];
    if (typeof value === "number" && Number.isFinite(value)) {
      counts[key] = Math.max(value, 0);
      hasOutcomeCounts = true;
    }
  }
  if (
    !hasOutcomeCounts &&
    typeof job.indexed_paper_count === "number" &&
    Number.isFinite(job.indexed_paper_count)
  ) {
    counts.indexed = Math.max(job.indexed_paper_count, 0);
  }
  return counts;
}

function sumTimelineOutcomeCounts(counts: TimelineOutcomeCounts) {
  return TIMELINE_OUTCOMES.reduce((sum, outcome) => sum + counts[outcome.key], 0);
}

function buildJobsTimeline(
  jobs: AdminJobOverviewRow[],
  timeWindow: JobsTimeWindow,
): TimelineSummary {
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
      maxTotal: 0,
      totalIndexed: 0,
      trackedJobs: 0,
    };
  }

  const selectedBucket = selectTimelineBucket(Math.max(timeWindow.endMs - timeWindow.startMs, 1));
  const startTimestamp =
    Math.floor(timeWindow.startMs / selectedBucket.sizeMs) * selectedBucket.sizeMs;
  const endTimestamp = Math.ceil(timeWindow.endMs / selectedBucket.sizeMs) * selectedBucket.sizeMs;
  const byBucket = new Map<number, { jobCount: number; outcomes: TimelineOutcomeCounts }>();

  for (const job of trackedJobs) {
    const bucketTimestamp =
      Math.floor(job.timestamp / selectedBucket.sizeMs) * selectedBucket.sizeMs;
    const current = byBucket.get(bucketTimestamp) ?? {
      jobCount: 0,
      outcomes: emptyTimelineOutcomeCounts(),
    };
    const counts = timelineOutcomeCountsForJob(job);
    for (const { key } of TIMELINE_OUTCOMES) {
      current.outcomes[key] += counts[key];
    }
    current.jobCount += 1;
    byBucket.set(bucketTimestamp, current);
  }

  const buckets: TimelineBucketPoint[] = [];
  for (let cursor = startTimestamp; cursor <= endTimestamp; cursor += selectedBucket.sizeMs) {
    const entry = byBucket.get(cursor) ?? { jobCount: 0, outcomes: emptyTimelineOutcomeCounts() };
    const outcomes = { ...entry.outcomes };
    buckets.push({
      axisLabel: formatTimelineAxisLabel(cursor, selectedBucket.sizeMs),
      bucketLabel: formatTimelineTooltipLabel(cursor, selectedBucket.sizeMs),
      jobCount: entry.jobCount,
      outcomes,
      totalCount: sumTimelineOutcomeCounts(outcomes),
    });
  }

  const latestJob = trackedJobs.reduce((current, candidate) =>
    candidate.timestamp > current.timestamp ? candidate : current,
  );

  return {
    bucketLabel: selectedBucket.label,
    buckets,
    latestUpdatedAt: latestJob.updated_at,
    maxTotal: buckets.reduce((max, bucket) => Math.max(max, bucket.totalCount), 0),
    totalIndexed: trackedJobs.reduce(
      (sum, job) => sum + timelineOutcomeCountsForJob(job).indexed,
      0,
    ),
    trackedJobs: trackedJobs.length,
  };
}

function readAdminJobsTheme(): AdminJobsTheme {
  if (typeof window === "undefined") {
    return {
      accent: "#7f99df",
      areaFill: "rgba(127, 153, 223, 0.12)",
      axisText: "#6b6259",
      barFill: "#9fb2e8",
      line: "rgba(27, 22, 17, 0.12)",
      text: "#1b1611",
      tooltipBackground: "#ffffff",
    };
  }
  const styles = getComputedStyle(document.body);
  const text = styles.getPropertyValue("--text").trim() || "#1b1611";
  const axisText = styles.getPropertyValue("--muted").trim() || "#6b6259";
  const line = styles.getPropertyValue("--line").trim() || "rgba(27, 22, 17, 0.12)";
  const isDark = document.body.classList.contains("authenticated-theme-dark");
  return {
    accent: isDark ? "#8ea6dd" : "#7f99df",
    areaFill: isDark ? "rgba(142, 166, 221, 0.14)" : "rgba(127, 153, 223, 0.12)",
    axisText,
    barFill: isDark ? "#aab9e8" : "#9fb2e8",
    line,
    text,
    tooltipBackground: isDark ? "#0f151d" : "#ffffff",
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

function CreateJobFieldHeader({
  jobKind,
  fieldName,
}: {
  jobKind: CreateJobKind;
  fieldName: string;
}) {
  const definition = createJobFieldDefinition(jobKind, fieldName);
  const fieldId = createJobFieldId(jobKind, fieldName);
  const tooltipId = `${fieldId}-tooltip`;
  return (
    <div className="admin-create-job-field-header">
      <label htmlFor={fieldId} className="settings-field-label">
        {definition.label}
      </label>
      <span
        className={`admin-create-job-field-badge ${definition.required ? "required" : "optional"}`}
        aria-hidden="true"
      >
        {definition.required ? "Required" : "Optional"}
      </span>
      <div className="admin-create-job-help">
        <button
          type="button"
          className="admin-create-job-help-trigger"
          aria-label={`Explain ${definition.label}`}
          aria-describedby={tooltipId}
          onClick={(event) => {
            event.preventDefault();
            event.stopPropagation();
          }}
        >
          i
        </button>
        <span id={tooltipId} role="tooltip" className="admin-create-job-help-tooltip">
          {createJobFieldHelpText(definition)}
        </span>
      </div>
    </div>
  );
}

function CreateJobInputField({
  jobKind,
  fieldName,
  ...inputProps
}: {
  jobKind: CreateJobKind;
  fieldName: string;
} & ComponentPropsWithoutRef<"input">) {
  const definition = createJobFieldDefinition(jobKind, fieldName);
  return (
    <div className="settings-field">
      <CreateJobFieldHeader jobKind={jobKind} fieldName={fieldName} />
      <input
        id={createJobFieldId(jobKind, fieldName)}
        name={fieldName}
        required={definition.required}
        {...inputProps}
      />
    </div>
  );
}

function CreateJobTextareaField({
  jobKind,
  fieldName,
  ...textareaProps
}: {
  jobKind: CreateJobKind;
  fieldName: string;
} & ComponentPropsWithoutRef<"textarea">) {
  const definition = createJobFieldDefinition(jobKind, fieldName);
  return (
    <div className="settings-field">
      <CreateJobFieldHeader jobKind={jobKind} fieldName={fieldName} />
      <textarea
        id={createJobFieldId(jobKind, fieldName)}
        name={fieldName}
        required={definition.required}
        {...textareaProps}
      />
    </div>
  );
}

function CreateJobCheckboxField({
  jobKind,
  fieldName,
}: {
  jobKind: CreateJobKind;
  fieldName: string;
}) {
  const definition = createJobFieldDefinition(jobKind, fieldName);
  const fieldId = createJobFieldId(jobKind, fieldName);
  const labelId = `${fieldId}-label`;
  const tooltipId = `${fieldId}-tooltip`;
  return (
    <div className="admin-create-job-checkbox-field">
      <input
        id={fieldId}
        name={fieldName}
        type="checkbox"
        aria-labelledby={labelId}
        aria-describedby={tooltipId}
      />
      <span className="admin-create-job-checkbox-copy">
        <span className="admin-create-job-checkbox-header">
          <label htmlFor={fieldId} className="settings-field-label" id={labelId}>
            {definition.label}
          </label>
          <span className="admin-create-job-field-badge optional" aria-hidden="true">
            Optional
          </span>
          <span className="admin-create-job-help">
            <button
              type="button"
              className="admin-create-job-help-trigger"
              aria-label={`Explain ${definition.label}`}
              aria-describedby={tooltipId}
              onClick={(event) => {
                event.preventDefault();
                event.stopPropagation();
              }}
            >
              i
            </button>
            <span id={tooltipId} role="tooltip" className="admin-create-job-help-tooltip">
              {createJobFieldHelpText(definition)}
            </span>
          </span>
        </span>
        <span className="admin-create-job-checkbox-note">
          Run a fresh indexing pass even if the paper is already stored.
        </span>
      </span>
    </div>
  );
}

function TimelineSkeleton({ message }: { message?: ReactNode }) {
  return (
    <div className="admin-jobs-skeleton-chart" data-testid="admin-jobs-chart-skeleton">
      <div className="admin-jobs-skeleton-grid">
        {Array.from({ length: 5 }, (_, index) => (
          <span key={`grid-${index}`} className="admin-jobs-skeleton-grid-line" />
        ))}
      </div>
      <div className="admin-jobs-skeleton-bars" aria-hidden="true">
        {TIMELINE_SKELETON_BAR_HEIGHTS.map((height, index) => (
          <span
            key={`bar-${index}`}
            className="admin-jobs-skeleton-bar"
            style={{ height: `${height * 100}%` }}
          />
        ))}
      </div>
      {message ? <div className="admin-jobs-chart-empty-note">{message}</div> : null}
    </div>
  );
}

function TableSkeletonRows({ columns, rows = 5 }: { columns: number; rows?: number }) {
  return (
    <>
      {Array.from({ length: rows }, (_, rowIndex) => (
        <tr key={`skeleton-row-${rowIndex}`} className="admin-jobs-skeleton-row">
          {Array.from({ length: columns }, (_, columnIndex) => (
            <td key={`skeleton-cell-${rowIndex}-${columnIndex}`}>
              <span
                className={`admin-jobs-skeleton-cell ${columnIndex === 0 ? "wide" : ""}`}
                aria-hidden="true"
              />
            </td>
          ))}
        </tr>
      ))}
    </>
  );
}

function JobsWindowControl({
  customRange,
  onCustomRangeChange,
  onSelectRangeId,
  selectedRangeId,
}: {
  customRange: CustomTimeRangeState;
  onCustomRangeChange: (range: CustomTimeRangeState) => void;
  onSelectRangeId: (nextRangeId: JobsTimeRangeId) => void;
  selectedRangeId: JobsTimeRangeId;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [customPopoverOpen, setCustomPopoverOpen] = useState(
    selectedRangeId === CUSTOM_TIME_RANGE_ID,
  );
  const selectedLabel =
    JOBS_TIME_RANGE_PRESETS.find((preset) => preset.id === selectedRangeId)?.label ??
    "Custom range";

  useEffect(() => {
    setCustomPopoverOpen(selectedRangeId === CUSTOM_TIME_RANGE_ID);
  }, [selectedRangeId]);

  function selectWindow(nextRangeId: JobsTimeRangeId) {
    setMenuOpen(false);
    if (nextRangeId === CUSTOM_TIME_RANGE_ID) {
      setCustomPopoverOpen(true);
    }
    onSelectRangeId(nextRangeId);
  }

  return (
    <div className="admin-jobs-window-control">
      <button
        type="button"
        className="admin-compact-action admin-compact-action-window admin-jobs-window-button"
        aria-haspopup="listbox"
        aria-expanded={menuOpen}
        aria-label="Jobs window"
        onClick={() => setMenuOpen((open) => !open)}
      >
        <span>{selectedLabel}</span>
      </button>
      {menuOpen ? (
        <div className="admin-jobs-window-menu" role="listbox" aria-label="Jobs window">
          {JOBS_TIME_RANGE_PRESETS.map((preset) => (
            <button
              key={preset.id}
              type="button"
              className={`admin-jobs-window-option${selectedRangeId === preset.id ? " active" : ""}`}
              role="option"
              aria-selected={selectedRangeId === preset.id}
              onClick={() => selectWindow(preset.id)}
            >
              {preset.label}
            </button>
          ))}
          <button
            type="button"
            className={`admin-jobs-window-option${selectedRangeId === CUSTOM_TIME_RANGE_ID ? " active" : ""}`}
            role="option"
            aria-selected={selectedRangeId === CUSTOM_TIME_RANGE_ID}
            onClick={() => selectWindow(CUSTOM_TIME_RANGE_ID)}
          >
            Custom range
          </button>
        </div>
      ) : null}
      {selectedRangeId === CUSTOM_TIME_RANGE_ID && customPopoverOpen && !menuOpen ? (
        <div className="admin-jobs-custom-popover" role="dialog" aria-label="Custom job window">
          <label className="admin-jobs-custom-field">
            <span>Start</span>
            <input
              type="datetime-local"
              aria-label="Custom range start"
              value={customRange.start}
              onChange={(event) =>
                onCustomRangeChange({ ...customRange, start: event.target.value })
              }
            />
          </label>
          <label className="admin-jobs-custom-field">
            <span>End</span>
            <input
              type="datetime-local"
              aria-label="Custom range end"
              value={customRange.end}
              onChange={(event) => onCustomRangeChange({ ...customRange, end: event.target.value })}
            />
          </label>
          <button
            type="button"
            className="admin-jobs-custom-close"
            onClick={() => setCustomPopoverOpen(false)}
          >
            Done
          </button>
        </div>
      ) : null}
    </div>
  );
}

function JobsRefreshControl({
  onSelectRefreshId,
  selectedRefreshId,
}: {
  onSelectRefreshId: (nextRefreshId: JobsRefreshIntervalId) => void;
  selectedRefreshId: JobsRefreshIntervalId;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const selectedLabel =
    JOBS_REFRESH_INTERVAL_OPTIONS.find((option) => option.id === selectedRefreshId)?.label ??
    "30 seconds";

  function selectRefresh(nextRefreshId: JobsRefreshIntervalId) {
    setMenuOpen(false);
    onSelectRefreshId(nextRefreshId);
  }

  return (
    <div className="admin-jobs-refresh-control">
      <button
        type="button"
        className="admin-compact-action admin-compact-action-window admin-jobs-refresh-button"
        aria-haspopup="listbox"
        aria-expanded={menuOpen}
        aria-label="Jobs refresh interval"
        onClick={() => setMenuOpen((open) => !open)}
      >
        <span>{selectedLabel}</span>
      </button>
      {menuOpen ? (
        <div className="admin-jobs-refresh-menu" role="listbox" aria-label="Jobs refresh interval">
          {JOBS_REFRESH_INTERVAL_OPTIONS.map((option) => (
            <button
              key={option.id}
              type="button"
              className={`admin-jobs-refresh-option${selectedRefreshId === option.id ? " active" : ""}`}
              role="option"
              aria-selected={selectedRefreshId === option.id}
              onClick={() => selectRefresh(option.id)}
            >
              {option.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function JobsTimeline({
  jobs,
  loading,
  timeWindow,
}: {
  jobs: AdminJobOverviewRow[];
  loading: boolean;
  timeWindow: JobsTimeWindow;
}) {
  const theme = readAdminJobsTheme();
  const timeline = useMemo(() => buildJobsTimeline(jobs, timeWindow), [jobs, timeWindow]);
  const showSkeleton = loading || timeline.trackedJobs === 0;
  const isZeroTimeline = timeline.maxTotal === 0;

  const option = useMemo<EChartsOption>(() => {
    return {
      animation: false,
      animationDuration: 0,
      animationDurationUpdate: 0,
      backgroundColor: "transparent",
      stateAnimation: {
        duration: 0,
      },
      textStyle: {
        color: theme.text,
        fontFamily: '"JetBrains Mono", "SFMono-Regular", monospace',
      },
      grid: { top: 8, right: 0, bottom: 0, left: 0 },
      tooltip: {
        trigger: "axis",
        axisPointer: {
          type: "shadow",
          shadowStyle: { color: theme.areaFill },
        },
        backgroundColor: theme.tooltipBackground,
        borderColor: theme.line,
        textStyle: {
          color: theme.text,
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
            `${formatWholeNumber(bucket.outcomes.indexed)} indexed`,
            `${formatWholeNumber(bucket.outcomes.skipped)} skipped`,
            `${formatWholeNumber(bucket.outcomes.failed)} failed`,
            `${formatWholeNumber(bucket.jobCount)} tracked jobs`,
          ].join("<br/>");
        },
      },
      xAxis: {
        type: "category",
        data: timeline.buckets.map((bucket) => bucket.axisLabel),
        boundaryGap: true,
        axisLine: { lineStyle: { color: theme.line } },
        axisTick: { show: false },
        axisLabel: {
          color: theme.axisText,
          hideOverlap: true,
          show: false,
        },
      },
      yAxis: {
        type: "value",
        max: isZeroTimeline ? 8 : undefined,
        minInterval: 1,
        splitNumber: isZeroTimeline ? 8 : 5,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: {
          color: theme.axisText,
          show: false,
          formatter: (value: number) =>
            value >= 1000 ? `${(value / 1000).toFixed(value >= 10000 ? 0 : 1)}k` : `${value}`,
        },
        splitLine: {
          lineStyle: { color: theme.line },
        },
      },
      series: TIMELINE_OUTCOMES.map((outcome, index) => ({
        name: outcome.label,
        type: "bar",
        stack: "paper outcomes",
        data: timeline.buckets.map((bucket) => bucket.outcomes[outcome.key]),
        barMaxWidth: 10,
        itemStyle: {
          color: outcome.color,
          borderRadius: index === TIMELINE_OUTCOMES.length - 1 ? [4, 4, 0, 0] : 0,
        },
        emphasis: {
          itemStyle: {
            color: outcome.color,
          },
        },
      })),
    };
  }, [isZeroTimeline, theme, timeline]);

  const emptyChartMessage = (
    <>
      <strong>No indexed papers in this window</strong>
      <span>Pick another window, widen the range, or run a backfill job.</span>
    </>
  );

  return (
    <section className="admin-jobs-timeline-panel" aria-label="Indexed papers timeline">
      {showSkeleton ? (
        <TimelineSkeleton message={!loading ? emptyChartMessage : undefined} />
      ) : (
        <div className="admin-jobs-chart-shell" data-testid="admin-jobs-chart">
          <ReactECharts
            option={option}
            className="admin-jobs-chart"
            style={{ height: "320px", width: "100%" }}
            notMerge
            lazyUpdate
          />
        </div>
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
  const [selectedRangeId, setSelectedRangeId] = useState<JobsTimeRangeId>("7d");
  const [selectedRefreshId, setSelectedRefreshId] = useState<JobsRefreshIntervalId>("30s");
  const [customRange, setCustomRange] = useState<CustomTimeRangeState>(() =>
    defaultCustomTimeRange(),
  );
  const jobsRefreshInFlightRef = useRef(false);

  const [jobPapers, setJobPapers] = useState<AdminJobPaperResponse[]>([]);
  const [jobPapersLoading, setJobPapersLoading] = useState(false);
  const [jobPapersError, setJobPapersError] = useState("");

  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [createJobKind, setCreateJobKind] = useState<CreateJobKind | null>(null);
  const [createSubmitting, setCreateSubmitting] = useState(false);
  const createSubmittingRef = useRef(false);

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

  const timeWindow = useMemo(
    () =>
      selectedRangeId === CUSTOM_TIME_RANGE_ID
        ? customTimeWindow(customRange)
        : presetTimeWindow(selectedRangeId),
    [customRange, selectedRangeId],
  );
  const filteredJobs = useMemo(() => jobsInTimeWindow(jobs, timeWindow), [jobs, timeWindow]);
  const selectedRefreshInterval = useMemo(
    () =>
      JOBS_REFRESH_INTERVAL_OPTIONS.find((option) => option.id === selectedRefreshId) ??
      JOBS_REFRESH_INTERVAL_OPTIONS[1],
    [selectedRefreshId],
  );

  const refreshJobs = useCallback(
    async ({ showLoading = true }: { showLoading?: boolean } = {}) => {
      if (jobsRefreshInFlightRef.current) {
        return;
      }
      jobsRefreshInFlightRef.current = true;
      if (showLoading) {
        setJobsLoading(true);
      }
      setJobsError("");
      try {
        const response = await loadBrowserAdminJobsOverview(ADMIN_JOBS_OVERVIEW_LIMIT);
        setJobs(response.jobs);
      } catch (error) {
        setJobsError(panelErrorMessage(error, "Jobs could not be loaded."));
      } finally {
        jobsRefreshInFlightRef.current = false;
        setJobsLoading(false);
      }
    },
    [],
  );

  const refreshJobPapers = useCallback(async (rootJobId: string) => {
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
  }, []);

  useEffect(() => {
    if (view.kind === "jobs") {
      void refreshJobs({ showLoading: true });
    }
  }, [refreshJobs, view.kind]);

  useEffect(() => {
    if (view.kind !== "jobs") {
      return;
    }
    const intervalId = window.setInterval(() => {
      void refreshJobs({ showLoading: false });
    }, selectedRefreshInterval.intervalMs);
    return () => {
      window.clearInterval(intervalId);
    };
  }, [refreshJobs, selectedRefreshInterval.intervalMs, view.kind]);

  useEffect(() => {
    if (view.kind === "papers") {
      void refreshJobPapers(view.rootJobId);
    }
  }, [refreshJobPapers, view.kind, view.kind === "papers" ? view.rootJobId : null]);

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
    if (!createJobKind || createSubmittingRef.current) {
      return;
    }
    createSubmittingRef.current = true;
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
          chunk_months: parseIntegerWithDefault(formData.get("chunk_months"), 3),
          source: "arxiv",
        });
      } else if (createJobKind === "harvest_top_cited") {
        await queueBrowserAdminHarvestTopCited({
          min_citations: parseIntegerWithDefault(formData.get("min_citations"), 100),
          max_papers: parseOptionalInteger(formData.get("max_papers")),
          chunk_size: parseIntegerWithDefault(formData.get("chunk_size"), 50),
        });
      } else if (createJobKind === "harvest_venue") {
        await queueBrowserAdminHarvestVenue({
          venue: String(formData.get("venue") ?? "").trim(),
          year: parseIntegerWithDefault(formData.get("year"), 2024),
          max_papers: parseOptionalInteger(formData.get("max_papers")),
          chunk_size: parseIntegerWithDefault(formData.get("chunk_size"), 50),
        });
      } else {
        await queueBrowserAdminEnrichMissingMetadata(
          parseIntegerWithDefault(formData.get("limit"), 50000),
          parseIntegerWithDefault(formData.get("min_age_seconds"), 3600),
        );
      }
      onNote(`Queued ${activeCreateJob?.label ?? "job"}.`);
      setCreateModalOpen(false);
      setCreateJobKind(null);
      await refreshJobs({ showLoading: false });
    } catch (error) {
      onError(panelErrorMessage(error, "Job could not be queued."));
    } finally {
      createSubmittingRef.current = false;
      setCreateSubmitting(false);
    }
  }

  function handleSelectRangeId(nextRangeId: JobsTimeRangeId) {
    if (nextRangeId === CUSTOM_TIME_RANGE_ID && selectedRangeId !== CUSTOM_TIME_RANGE_ID) {
      const seedWindow =
        selectedRangeId === CUSTOM_TIME_RANGE_ID
          ? customTimeWindow(customRange)
          : presetTimeWindow(selectedRangeId);
      setCustomRange({
        end: formatDateTimeLocalValue(new Date(seedWindow.endMs)),
        start: formatDateTimeLocalValue(new Date(seedWindow.startMs)),
      });
    }
    setSelectedRangeId(nextRangeId);
  }

  return (
    <section className="admin-jobs-shell">
      {view.kind === "jobs" ? (
        <>
          <div className="admin-jobs-header">
            <div className="admin-jobs-header-title">
              <h1>Control panel</h1>
              <p>Queue and monitor jobs</p>
            </div>
            <div className="admin-jobs-header-actions">
              <JobsWindowControl
                customRange={customRange}
                onCustomRangeChange={setCustomRange}
                onSelectRangeId={handleSelectRangeId}
                selectedRangeId={selectedRangeId}
              />
              <JobsRefreshControl
                onSelectRefreshId={setSelectedRefreshId}
                selectedRefreshId={selectedRefreshId}
              />
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
          </div>

          {jobsError ? <div className="admin-jobs-inline-error">{jobsError}</div> : null}
          <div className="admin-jobs-surface">
            <JobsTimeline jobs={filteredJobs} loading={jobsLoading} timeWindow={timeWindow} />
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
                <tbody
                  data-testid={
                    jobsLoading || filteredJobs.length === 0
                      ? "admin-jobs-table-skeleton"
                      : undefined
                  }
                >
                  {jobsLoading || filteredJobs.length === 0 ? (
                    <TableSkeletonRows columns={5} />
                  ) : (
                    filteredJobs.map((job) => {
                      const expanded = Boolean(expandedJobIds[job.job_id]);
                      const detailPairs = detailRows(job);
                      return (
                        <FragmentRow key={job.job_id}>
                          <tr
                            className={`admin-jobs-row${expanded ? " expanded" : ""}`}
                            onClick={() =>
                              setExpandedJobIds((current) => ({
                                ...current,
                                [job.job_id]: !current[job.job_id],
                              }))
                            }
                          >
                            <td>
                              <div className="admin-jobs-type-cell">
                                <button
                                  type="button"
                                  className="admin-jobs-expand"
                                  onClick={(event) => {
                                    event.stopPropagation();
                                    setExpandedJobIds((current) => ({
                                      ...current,
                                      [job.job_id]: !current[job.job_id],
                                    }));
                                  }}
                                  aria-expanded={expanded}
                                >
                                  <span>{expanded ? "−" : "+"}</span>
                                  <span>{jobTypeLabel(job.job_type)}</span>
                                </button>
                                <span className="admin-jobs-run-reason">
                                  {adminJobRunReason(job)}
                                </span>
                              </div>
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
                                  onClick={(event) => {
                                    event.stopPropagation();
                                    onOpenJobPapers(job.root_job_id);
                                  }}
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
          </div>
        </>
      ) : (
        <>
          <div className="admin-jobs-header admin-jobs-header-papers">
            <div className="admin-jobs-header-main">
              <button
                type="button"
                className="admin-jobs-back-icon"
                aria-label="Back to jobs"
                onClick={onBackToJobs}
              >
                ←
              </button>
              <h1>Indexed papers</h1>
            </div>
            <div className="admin-jobs-paper-meta">
              <span>
                {jobPapersLoading
                  ? "Loading papers"
                  : `${formatWholeNumber(jobPapers.length)} ${
                      jobPapers.length === 1 ? "paper" : "papers"
                    }`}
              </span>
              <code>{view.rootJobId}</code>
            </div>
          </div>

          {jobPapersError ? <div className="admin-jobs-inline-error">{jobPapersError}</div> : null}
          <div className="admin-jobs-surface">
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
          <div
            className={`modal-panel admin-create-job-modal${
              activeCreateJob ? " admin-create-job-modal-form" : ""
            }`}
          >
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
                aria-busy={createSubmitting}
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
                    <CreateJobInputField
                      jobKind="index_paper"
                      fieldName="arxiv_id"
                      placeholder="2401.01234"
                    />
                    <CreateJobCheckboxField jobKind="index_paper" fieldName="force" />
                  </>
                ) : null}

                {createJobKind === "harvest_range" ? (
                  <>
                    <CreateJobTextareaField
                      jobKind="harvest_range"
                      fieldName="categories"
                      rows={3}
                      placeholder="cs.LG, cs.CL"
                    />
                    <div className="admin-create-job-grid">
                      <CreateJobInputField
                        jobKind="harvest_range"
                        fieldName="start_date"
                        placeholder="2024-01-01"
                        inputMode="numeric"
                        pattern={DATE_PATTERN}
                      />
                      <CreateJobInputField
                        jobKind="harvest_range"
                        fieldName="end_date"
                        placeholder="2024-03-31"
                        inputMode="numeric"
                        pattern={DATE_PATTERN}
                      />
                    </div>
                    <div className="admin-create-job-grid">
                      <CreateJobInputField
                        jobKind="harvest_range"
                        fieldName="limit_per_window"
                        type="number"
                        inputMode="numeric"
                        min={1}
                        max={10000000}
                        step={1}
                        placeholder="1000"
                      />
                      <CreateJobInputField
                        jobKind="harvest_range"
                        fieldName="chunk_months"
                        type="number"
                        inputMode="numeric"
                        min={1}
                        max={24}
                        step={1}
                        defaultValue="3"
                      />
                    </div>
                  </>
                ) : null}

                {createJobKind === "harvest_top_cited" ? (
                  <>
                    <div className="admin-create-job-grid">
                      <CreateJobInputField
                        jobKind="harvest_top_cited"
                        fieldName="min_citations"
                        type="number"
                        inputMode="numeric"
                        min={0}
                        max={1000000}
                        step={1}
                        defaultValue="100"
                      />
                      <CreateJobInputField
                        jobKind="harvest_top_cited"
                        fieldName="chunk_size"
                        type="number"
                        inputMode="numeric"
                        min={1}
                        max={500}
                        step={1}
                        defaultValue="50"
                      />
                    </div>
                    <CreateJobInputField
                      jobKind="harvest_top_cited"
                      fieldName="max_papers"
                      type="number"
                      inputMode="numeric"
                      min={1}
                      max={10000000}
                      step={1}
                      placeholder="Optional"
                    />
                  </>
                ) : null}

                {createJobKind === "harvest_venue" ? (
                  <>
                    <div className="admin-create-job-grid">
                      <CreateJobInputField
                        jobKind="harvest_venue"
                        fieldName="venue"
                        placeholder="ICLR"
                      />
                      <CreateJobInputField
                        jobKind="harvest_venue"
                        fieldName="year"
                        type="number"
                        inputMode="numeric"
                        min={1900}
                        max={2100}
                        step={1}
                        defaultValue="2024"
                      />
                    </div>
                    <div className="admin-create-job-grid">
                      <CreateJobInputField
                        jobKind="harvest_venue"
                        fieldName="max_papers"
                        type="number"
                        inputMode="numeric"
                        min={1}
                        max={10000000}
                        step={1}
                        placeholder="Optional"
                      />
                      <CreateJobInputField
                        jobKind="harvest_venue"
                        fieldName="chunk_size"
                        type="number"
                        inputMode="numeric"
                        min={1}
                        max={500}
                        step={1}
                        defaultValue="50"
                      />
                    </div>
                  </>
                ) : null}

                {createJobKind === "enrich_missing_metadata" ? (
                  <>
                    <div className="admin-create-job-grid">
                      <CreateJobInputField
                        jobKind="enrich_missing_metadata"
                        fieldName="limit"
                        type="number"
                        inputMode="numeric"
                        min={1}
                        max={5000000}
                        step={1}
                        defaultValue="50000"
                      />
                      <CreateJobInputField
                        jobKind="enrich_missing_metadata"
                        fieldName="min_age_seconds"
                        type="number"
                        inputMode="numeric"
                        min={0}
                        max={2592000}
                        step={1}
                        defaultValue="3600"
                      />
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
