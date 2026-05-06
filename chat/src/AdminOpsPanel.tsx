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
  browserAdminPaperAssetContentUrl,
  browserPaperPreviewContentUrl,
  getBrowserAdminPaperDetail,
  getBrowserAdminPaperAsset,
  getBrowserAdminPaperFile,
  getBrowserPaperPreview,
  loadBrowserAdminJobPapers,
  loadBrowserAdminJobsOverview,
  loadBrowserAdminPaperFiles,
  loadBrowserAdminPapers,
  queueBrowserAdminEnrichMissingMetadata,
  queueBrowserAdminHarvestRange,
  queueBrowserAdminHarvestTopCited,
  queueBrowserAdminHarvestVenue,
  queueBrowserAdminPaperIndex,
} from "./api";
import type {
  AdminJobOverviewRow,
  AdminJobPaperResponse,
  AdminPaperJobTouchResponse,
  CodeFileListResponse,
  FileContentResponse,
  FileObject,
  PaperAssetResponse,
  PaperPreviewResponse,
  PaperResponse,
} from "./types";
import { formatLocalTimestamp } from "./time";

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

type PaperModalTab = "overview" | "screenshot" | "metadata" | "jobs" | "files" | "code";

type FileTreeNode =
  | {
      kind: "file";
      name: string;
      path: string;
      file: FileObject;
    }
  | {
      kind: "folder";
      name: string;
      path: string;
      children: FileTreeNode[];
    };

const PAPER_MODAL_TABS: Array<{
  asset: string;
  id: PaperModalTab;
  label: string;
}> = [
  { id: "overview", label: "Overview", asset: "notes.svg" },
  { id: "screenshot", label: "Screenshot", asset: "notes.svg" },
  { id: "metadata", label: "Metadata", asset: "data.svg" },
  { id: "jobs", label: "Jobs", asset: "data.svg" },
  { id: "files", label: "File structure", asset: "code.svg" },
  { id: "code", label: "Code", asset: "code.svg" },
];

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

const JOB_TYPE_LABELS: Record<string, string> = {
  index_paper: "Single paper",
  harvest_range: "Date range",
  harvest_top_cited: "Top cited",
  harvest_venue: "Venue year",
  harvest_chunk: "Harvest chunk",
  harvest_author: "Author harvest",
  enrich_missing_metadata: "Metadata enrichment",
  ingest_repo: "Code ingest",
  latest_sync_schedule: "Latest sync",
  reindex_embeddings: "Reindex embeddings",
  reindex_missing_embeddings: "Reindex missing embeddings",
  summarize_top_cited: "Summarize top cited",
  sync_latest: "Latest sync",
};

const JOB_TYPE_DESCRIPTIONS: Record<string, string> = {
  index_paper:
    "Index one paper from arXiv so it becomes searchable and its artifacts land in storage.",
  harvest_range:
    "Walk a date window across one or more arXiv categories and index each matching paper in chunks.",
  harvest_top_cited:
    "Ask Semantic Scholar for highly cited papers, then index the arXiv-linked ones in batches.",
  harvest_venue:
    "Fetch papers from one conference or journal year, then index the arXiv-linked subset.",
  harvest_chunk:
    "Worker chunk for harvest planners; handles arXiv range chunks plus Semantic Scholar top-cited, venue, and author chunks.",
  harvest_author:
    "Find papers from top authors derived from the indexed corpus, then enqueue harvest chunks for them.",
  enrich_missing_metadata:
    "Revisit already stored papers and fill in missing Semantic Scholar fields like citations and TLDRs.",
  ingest_repo: "Fetch and index GitHub repository artifacts for a paper with a code URL.",
  latest_sync_schedule: "Scheduled latest-paper sync from configured arXiv categories.",
  reindex_embeddings: "Rewrite paper and chunk embeddings for indexed papers.",
  reindex_missing_embeddings: "Fill only missing paper and chunk embeddings.",
  summarize_top_cited: "Summarize already-indexed high-citation papers.",
  sync_latest: "Scheduled latest-paper sync from configured arXiv categories.",
};

const JOB_TYPE_OPTIONS: Array<{
  kind: CreateJobKind;
  label: string;
  description: string;
  estimate: string;
}> = [
  {
    kind: "index_paper",
    label: "Single paper",
    description: JOB_TYPE_DESCRIPTIONS.index_paper,
    estimate: "Usually 1-3 minutes.",
  },
  {
    kind: "harvest_range",
    label: "Date range",
    description: JOB_TYPE_DESCRIPTIONS.harvest_range,
    estimate: "Usually 10 minutes to a few hours, depending on the date span and limits.",
  },
  {
    kind: "harvest_top_cited",
    label: "Top cited",
    description: JOB_TYPE_DESCRIPTIONS.harvest_top_cited,
    estimate: "Usually 15 minutes to a few hours, depending on the paper cap.",
  },
  {
    kind: "harvest_venue",
    label: "Venue year",
    description: JOB_TYPE_DESCRIPTIONS.harvest_venue,
    estimate: "Usually 15 minutes to a few hours, depending on the venue size.",
  },
  {
    kind: "enrich_missing_metadata",
    label: "Metadata enrichment",
    description: JOB_TYPE_DESCRIPTIONS.enrich_missing_metadata,
    estimate: "Usually a few minutes to an hour, depending on how many papers need backfill.",
  },
];

const HOUR_MS = 60 * 60 * 1000;
const DAY_MS = 24 * HOUR_MS;
const ADMIN_JOBS_OVERVIEW_LIMIT = 200;
const ADMIN_JOB_PAPERS_PAGE_SIZE = 100;
const CUSTOM_TIME_RANGE_ID = "custom";
const TARGET_TIMELINE_BUCKET_COUNT = 96;
const TIMELINE_BUCKETS = [
  { sizeMs: 60 * 1000, label: "1-minute buckets" },
  { sizeMs: 2 * 60 * 1000, label: "2-minute buckets" },
  { sizeMs: 5 * 60 * 1000, label: "5-minute buckets" },
  { sizeMs: 10 * 60 * 1000, label: "10-minute buckets" },
  { sizeMs: 15 * 60 * 1000, label: "15-minute buckets" },
  { sizeMs: 20 * 60 * 1000, label: "20-minute buckets" },
  { sizeMs: 30 * 60 * 1000, label: "30-minute buckets" },
  { sizeMs: 45 * 60 * 1000, label: "45-minute buckets" },
  { sizeMs: HOUR_MS, label: "1-hour buckets" },
  { sizeMs: 90 * 60 * 1000, label: "90-minute buckets" },
  { sizeMs: 2 * HOUR_MS, label: "2-hour buckets" },
  { sizeMs: 3 * HOUR_MS, label: "3-hour buckets" },
  { sizeMs: 4 * HOUR_MS, label: "4-hour buckets" },
  { sizeMs: 6 * HOUR_MS, label: "6-hour buckets" },
  { sizeMs: 8 * HOUR_MS, label: "8-hour buckets" },
  { sizeMs: 12 * HOUR_MS, label: "12-hour buckets" },
  { sizeMs: DAY_MS, label: "Daily buckets" },
  { sizeMs: 2 * DAY_MS, label: "2-day buckets" },
  { sizeMs: 3 * DAY_MS, label: "3-day buckets" },
  { sizeMs: 6 * DAY_MS, label: "6-day buckets" },
];
const INTEGER_FORMATTER = new Intl.NumberFormat("en-US");

function adminStaticAssetPath(name: string) {
  return `${import.meta.env.BASE_URL}${name.replace(/^\/+/, "")}`;
}

function AdminPaperIcon({ asset, className = "" }: { asset: string; className?: string }) {
  return (
    <img
      src={adminStaticAssetPath(asset)}
      alt=""
      aria-hidden="true"
      className={className ? `icon-image ${className}` : "icon-image"}
    />
  );
}

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

type TimelineOutcomeKey = "indexed" | "updated" | "skipped" | "failed";

type TimelineOutcomeCounts = Record<TimelineOutcomeKey, number>;

const TIMELINE_OUTCOMES: Array<{ key: TimelineOutcomeKey; label: string; color: string }> = [
  { key: "indexed", label: "Indexed", color: "#432818" },
  { key: "updated", label: "Updated", color: "#2563eb" },
  { key: "skipped", label: "Skipped", color: "#99582a" },
  { key: "failed", label: "Failed", color: "#b42318" },
];
const SKIPPED_REASON_COLORS = ["#99582a", "#b7774d", "#c89a72", "#7f4a24"];

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
  trackedJobs: number;
};

type JobPaperStatusSegment = {
  color: string;
  count: number;
  label: string;
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
  return `${formatLocalTimestamp(startMs)} to ${formatLocalTimestamp(endMs)}`;
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
  return formatLocalTimestamp(value);
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

function truncateText(value: string, maxLength: number) {
  if (value.length <= maxLength) {
    return value;
  }
  return `${value.slice(0, maxLength).trimEnd()}...`;
}

function humanizeReason(value?: string | null) {
  const text = (value ?? "").trim();
  if (!text) {
    return "No reason recorded";
  }
  const labels: Record<string, string> = {
    already_indexed: "Already indexed",
    unknown: "No reason recorded",
  };
  return (
    labels[text] ??
    text
      .replaceAll("_", " ")
      .replaceAll(":", ": ")
      .replace(/\s+/g, " ")
      .trim()
      .replace(/^\w/, (match) => match.toUpperCase())
  );
}

function jobTypeLabel(jobType: string) {
  return JOB_TYPE_LABELS[jobType] ?? jobType.replaceAll("_", " ");
}

function jobTypeDescription(jobType: string) {
  return JOB_TYPE_DESCRIPTIONS[jobType] ?? "Worker job tracked by Paperbase.";
}

function JobTypeCell({
  expanded,
  job,
  onToggle,
}: {
  expanded: boolean;
  job: AdminJobOverviewRow;
  onToggle: () => void;
}) {
  const label = jobTypeLabel(job.job_type);
  const description = jobTypeDescription(job.job_type);
  const tooltipId = `admin-job-type-help-${job.job_id}`;
  return (
    <div className="admin-jobs-type-cell">
      <button
        type="button"
        className="admin-jobs-expand"
        onClick={(event) => {
          event.stopPropagation();
          onToggle();
        }}
        aria-expanded={expanded}
      >
        <span>{expanded ? "−" : "+"}</span>
        <span>{label}</span>
      </button>
      <span className="admin-jobs-type-help">
        <button
          type="button"
          className="admin-jobs-type-help-trigger"
          aria-label={`Describe ${label}`}
          aria-describedby={tooltipId}
          title={description}
          onClick={(event) => {
            event.preventDefault();
            event.stopPropagation();
          }}
        >
          i
        </button>
        <span id={tooltipId} role="tooltip" className="admin-jobs-type-help-tooltip">
          {description}
        </span>
      </span>
    </div>
  );
}

type AdminJobTrigger = {
  label: string;
  title: string;
  tone: "internal" | "requested" | "scheduled" | "unknown";
};

const REQUESTED_JOB_TYPES = new Set([
  "enrich_missing_metadata",
  "harvest_range",
  "harvest_top_cited",
  "harvest_venue",
  "index_paper",
]);

const INTERNAL_JOB_TYPES = new Set(["ingest_repo", "reindex_embeddings", "summarize_top_cited"]);

function adminJobTrigger(job: AdminJobOverviewRow): AdminJobTrigger {
  if (
    job.is_synthetic ||
    job.job_type === "latest_sync_schedule" ||
    job.job_type === "sync_latest"
  ) {
    return {
      label: "Scheduled",
      title: "Env scheduled · latest sync",
      tone: "scheduled",
    };
  }
  if (REQUESTED_JOB_TYPES.has(job.job_type)) {
    return {
      label: "Requested",
      title: `On-demand request · ${jobTypeLabel(job.job_type).toLowerCase()}`,
      tone: "requested",
    };
  }
  if (INTERNAL_JOB_TYPES.has(job.job_type)) {
    return {
      label: "Internal",
      title: `System generated · ${jobTypeLabel(job.job_type).toLowerCase()}`,
      tone: "internal",
    };
  }
  return {
    label: "Unknown",
    title: `Origin unknown · ${jobTypeLabel(job.job_type).toLowerCase()}`,
    tone: "unknown",
  };
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

function paperArxivId(paper: PaperResponse | null, entry: AdminJobPaperResponse | null) {
  return paper?.arxiv_id?.trim() || entry?.arxiv_id?.trim() || "";
}

function paperArxivUrl(paper: PaperResponse | null, entry: AdminJobPaperResponse | null) {
  const arxivId = paperArxivId(paper, entry);
  if (arxivId) {
    return `https://arxiv.org/abs/${arxivId}`;
  }
  if (paper?.html_url?.trim()) {
    return paper.html_url.trim();
  }
  return "";
}

function buildFileTree(files: FileObject[]): FileTreeNode[] {
  type MutableFolderNode = {
    name: string;
    path: string;
    folders: Map<string, MutableFolderNode>;
    files: FileTreeNode[];
  };

  const root: MutableFolderNode = {
    name: "",
    path: "",
    folders: new Map(),
    files: [],
  };

  for (const file of files) {
    const parts = file.path.split("/").filter(Boolean);
    if (parts.length === 0) {
      continue;
    }
    const fileName = parts.at(-1) ?? file.path;
    let current = root;
    let currentPath = "";
    for (const part of parts.slice(0, -1)) {
      currentPath = currentPath ? `${currentPath}/${part}` : part;
      const existing = current.folders.get(part);
      if (existing) {
        current = existing;
        continue;
      }
      const created: MutableFolderNode = {
        name: part,
        path: currentPath,
        folders: new Map(),
        files: [],
      };
      current.folders.set(part, created);
      current = created;
    }
    current.files.push({
      kind: "file",
      name: fileName,
      path: file.path,
      file,
    });
  }

  const sortByName = (left: { name: string }, right: { name: string }) =>
    left.name.localeCompare(right.name);

  function finalize(node: MutableFolderNode): FileTreeNode[] {
    const folders = Array.from(node.folders.values())
      .sort(sortByName)
      .map((folder) => ({
        kind: "folder" as const,
        name: folder.name,
        path: folder.path,
        children: finalize(folder),
      }));
    const files = [...node.files].sort(sortByName);
    return [...folders, ...files];
  }

  return finalize(root);
}

function countTreeFolders(nodes: FileTreeNode[]): number {
  return nodes.reduce((count, node) => {
    if (node.kind !== "folder") {
      return count;
    }
    return count + 1 + countTreeFolders(node.children);
  }, 0);
}

function selectTimelineBucket(spanMs: number) {
  return (
    TIMELINE_BUCKETS.find(
      (bucket) => Math.ceil(spanMs / bucket.sizeMs) <= TARGET_TIMELINE_BUCKET_COUNT,
    ) ?? TIMELINE_BUCKETS.at(-1)!
  );
}

function jobsInTimeWindow(jobs: AdminJobOverviewRow[], timeWindow: JobsTimeWindow) {
  return jobs.filter((job) => {
    if (job.is_synthetic || job.job_type === "latest_sync_schedule") {
      return true;
    }
    const timestamp = new Date(job.updated_at).getTime();
    return (
      !Number.isNaN(timestamp) && timestamp >= timeWindow.startMs && timestamp <= timeWindow.endMs
    );
  });
}

function formatTimelineAxisLabel(timestamp: number, bucketMs: number) {
  const date = new Date(timestamp);
  if (bucketMs < DAY_MS) {
    return date.toLocaleString([], {
      day: "numeric",
      hour: "numeric",
      hour12: true,
      month: "short",
    });
  }
  return date.toLocaleDateString([], { day: "numeric", month: "short" });
}

function formatTimelineTooltipLabel(timestamp: number, bucketMs: number) {
  const start = new Date(timestamp);
  const end = new Date(timestamp + bucketMs);
  if (bucketMs >= DAY_MS) {
    return `${formatLocalTimestamp(start)} to ${formatLocalTimestamp(end.getTime() - 1)}`;
  }
  return `${formatLocalTimestamp(start)} to ${formatLocalTimestamp(end)}`;
}

function emptyTimelineOutcomeCounts(): TimelineOutcomeCounts {
  return { indexed: 0, updated: 0, skipped: 0, failed: 0 };
}

function trackedPaperCountForJob(job: AdminJobOverviewRow) {
  if (typeof job.tracked_paper_count === "number" && Number.isFinite(job.tracked_paper_count)) {
    return Math.max(job.tracked_paper_count, 0);
  }
  if (typeof job.indexed_paper_count === "number" && Number.isFinite(job.indexed_paper_count)) {
    return Math.max(job.indexed_paper_count, 0);
  }
  return null;
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

function countJobPapersByOutcome(papers: AdminJobPaperResponse[]) {
  return papers.reduce<Record<string, number>>((counts, paper) => {
    counts[paper.outcome] = (counts[paper.outcome] ?? 0) + 1;
    return counts;
  }, {});
}

function countSkippedJobPapersByReason(papers: AdminJobPaperResponse[]) {
  return papers.reduce<Record<string, number>>((counts, paper) => {
    if (paper.outcome !== "skipped") {
      return counts;
    }
    const reason = (paper.skip_reason ?? "unknown").trim() || "unknown";
    counts[reason] = (counts[reason] ?? 0) + 1;
    return counts;
  }, {});
}

function buildJobPaperStatusSegments(
  outcomeCounts: Record<string, number>,
  skippedReasonCounts: Record<string, number>,
): JobPaperStatusSegment[] {
  const segments: JobPaperStatusSegment[] = [];
  const indexed = Math.max(0, outcomeCounts.indexed ?? 0);
  const updated = Math.max(0, outcomeCounts.updated ?? 0);
  const skipped = Math.max(0, outcomeCounts.skipped ?? 0);
  const failed = Math.max(0, outcomeCounts.failed ?? 0);
  if (indexed > 0) {
    segments.push({ label: "Indexed", count: indexed, color: "#432818" });
  }
  if (updated > 0) {
    segments.push({ label: "Updated", count: updated, color: "#2563eb" });
  }

  const skippedReasonEntries = Object.entries(skippedReasonCounts)
    .map(([reason, count]) => [reason, Math.max(0, count)] as const)
    .filter(([, count]) => count > 0);
  const countedSkipped = skippedReasonEntries.reduce((sum, [, count]) => sum + count, 0);
  const normalizedSkippedReasonEntries =
    skipped > countedSkipped
      ? [...skippedReasonEntries, ["unknown", skipped - countedSkipped] as const]
      : skippedReasonEntries;
  normalizedSkippedReasonEntries.forEach(([reason, count], index) => {
    segments.push({
      label: `Skipped: ${humanizeReason(reason)}`,
      count,
      color: SKIPPED_REASON_COLORS[index % SKIPPED_REASON_COLORS.length] ?? "#99582a",
    });
  });

  if (failed > 0) {
    segments.push({ label: "Failed", count: failed, color: "#b42318" });
  }
  return segments;
}

function buildConicGradient(segments: JobPaperStatusSegment[]) {
  const total = segments.reduce((sum, segment) => sum + segment.count, 0);
  if (total <= 0) {
    return "conic-gradient(rgba(27, 22, 17, 0.08) 0deg 360deg)";
  }
  let cursor = 0;
  const stops = segments.map((segment) => {
    const start = cursor;
    cursor += (segment.count / total) * 360;
    return `${segment.color} ${start.toFixed(2)}deg ${cursor.toFixed(2)}deg`;
  });
  return `conic-gradient(${stops.join(", ")})`;
}

function buildJobsTimeline(
  jobs: AdminJobOverviewRow[],
  timeWindow: JobsTimeWindow,
): TimelineSummary {
  const trackedJobs = jobs
    .filter(
      (job) => job.tracking_available && trackedPaperCountForJob(job) !== null && job.updated_at,
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

function debugValue(value: unknown): ReactNode {
  if (value === null || typeof value === "undefined" || value === "") {
    return "—";
  }
  if (Array.isArray(value)) {
    return value.length > 0 ? value.join(", ") : "—";
  }
  if (typeof value === "object") {
    return <pre className="admin-jobs-debug-block">{JSON.stringify(value, null, 2)}</pre>;
  }
  return String(value);
}

function debugBlock(value: unknown): ReactNode {
  const text = typeof value === "string" ? value : JSON.stringify(value ?? "", null, 2);
  if (!text) {
    return "—";
  }
  return <pre className="admin-jobs-debug-block">{text}</pre>;
}

function baseDetailRows(job: AdminJobOverviewRow): Array<[string, ReactNode]> {
  const details = job.details ?? {};
  return [
    ["Root job id", debugValue(details.root_job_id ?? job.root_job_id)],
    ["Representative job id", debugValue(details.representative_job_id ?? job.job_id)],
    ["All job ids", debugValue(details.job_ids)],
    ["Child job ids", debugValue(details.child_job_ids)],
    ["Worker id", debugValue(details.worker_id)],
    ["Heartbeat", details.heartbeat_at ? formatTimestamp(String(details.heartbeat_at)) : "—"],
    [
      "Lease expires",
      details.lease_expires_at ? formatTimestamp(String(details.lease_expires_at)) : "—",
    ],
    ["Next run", details.next_run_at ? formatTimestamp(String(details.next_run_at)) : "—"],
    [
      "Last attempted",
      details.last_attempted_at ? formatTimestamp(String(details.last_attempted_at)) : "—",
    ],
    [
      "Cancel requested",
      details.cancel_requested_at ? formatTimestamp(String(details.cancel_requested_at)) : "—",
    ],
    ["Cloud Run log filter", debugBlock(details.cloud_run_log_filter)],
    ["Payload", debugBlock(details.payload_json)],
    ["Latest error job", debugValue(details.latest_error_job_id)],
    [
      "Latest error failed at",
      details.latest_error_failed_at
        ? formatTimestamp(String(details.latest_error_failed_at))
        : "—",
    ],
    [
      "Latest error permanent",
      typeof details.latest_error_permanent === "boolean"
        ? details.latest_error_permanent
          ? "Yes"
          : "No"
        : "—",
    ],
    [
      "Latest error next run",
      details.latest_error_next_run_at
        ? formatTimestamp(String(details.latest_error_next_run_at))
        : "—",
    ],
    ["Stack trace", details.latest_error_trace ? debugBlock(details.latest_error_trace) : "—"],
  ];
}

function detailRows(job: AdminJobOverviewRow): Array<[string, ReactNode]> {
  const details = job.details ?? {};
  let rows: Array<[string, ReactNode]> = [];
  if (job.job_type === "latest_sync_schedule") {
    rows = [
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
  } else if (job.job_type === "harvest_range") {
    rows = [
      ["Harvest id", String(details.harvest_id ?? job.root_job_id)],
      ["Categories", Array.isArray(details.categories) ? details.categories.join(", ") : "—"],
      ["Window", `${details.start_date ?? "—"} to ${details.end_date ?? "—"}`],
      ["Chunk months", String(details.chunk_months ?? "—")],
      ["Per-window limit", String(details.limit_per_window ?? "—")],
    ];
  } else if (job.job_type === "harvest_top_cited") {
    rows = [
      ["Harvest id", String(details.harvest_id ?? job.root_job_id)],
      ["Minimum citations", String(details.min_citations ?? "—")],
      ["Paper cap", String(details.max_papers ?? "—")],
      ["Chunk size", String(details.chunk_size ?? "—")],
    ];
  } else if (job.job_type === "harvest_venue") {
    rows = [
      ["Harvest id", String(details.harvest_id ?? job.root_job_id)],
      ["Venue", String(details.venue ?? "—")],
      ["Year", String(details.year ?? "—")],
      ["Paper cap", String(details.max_papers ?? "—")],
      ["Chunk size", String(details.chunk_size ?? "—")],
    ];
  } else if (job.job_type === "index_paper") {
    rows = [
      ["arXiv id", String(details.arxiv_id ?? "—")],
      ["Force reindex", details.force ? "Yes" : "No"],
    ];
  } else if (job.job_type === "enrich_missing_metadata") {
    rows = [
      ["Limit", String(details.limit ?? "—")],
      ["Min age (seconds)", String(details.min_age_seconds ?? "—")],
    ];
  }
  return [...rows, ...baseDetailRows(job)];
}

function metadataRows(paper: PaperResponse | null, entry: AdminJobPaperResponse | null) {
  if (!paper && !entry) {
    return [];
  }
  const codeCandidates = paper?.code_url_candidates?.filter(Boolean).join(", ") || "—";
  return [
    ["Paper id", paper?.paper_id ?? entry?.paper_id ?? "—"],
    ["arXiv id", paper?.arxiv_id ?? entry?.arxiv_id ?? "—"],
    ["Title", paper?.title ?? entry?.title ?? "—"],
    ["Authors", paper?.authors ?? "—"],
    ["Status", paper?.index_status ?? entry?.outcome ?? "—"],
    ["Summary", paper?.summary_short || "—"],
    ["Simple summary", paper?.summary_eli5 || "—"],
    ["Abstract", paper?.abstract || "—"],
    ["Categories", paper?.categories ?? "—"],
    ["Primary category", paper?.primary_category ?? "—"],
    ["Published", paper?.published ?? "—"],
    ["Updated", paper?.updated ?? "—"],
    ["Citations", paper ? String(paper.citation_count) : "—"],
    ["Influential citations", paper ? String(paper.influential_citation_count) : "—"],
    ["Semantic Scholar id", paper?.semantic_scholar_id ?? "—"],
    ["DOI", paper?.doi ?? "—"],
    ["Journal ref", paper?.journal_ref ?? "—"],
    ["Comment", paper?.comment ?? "—"],
    ["Venue", paper?.venue ?? "—"],
    ["Venue year", paper?.venue_year ? String(paper.venue_year) : "—"],
    ["Code URL", paper?.code_url ?? "—"],
    ["Code URL source", paper?.code_url_source ?? "—"],
    ["Code URL candidates", codeCandidates],
    ["Code status", paper?.code_index_status ?? "—"],
    [
      "Code language",
      paper?.code_is_python === true
        ? "Python"
        : paper?.code_is_python === false
          ? "Not Python"
          : "—",
    ],
    ["Code note", paper?.code_python_reason ?? "—"],
    ["Source type", paper?.source_type ?? "—"],
    ["Indexed at", paper?.indexed_at ?? "—"],
  ];
}

function paperSummaryBlocks(paper: PaperResponse | null) {
  if (!paper) {
    return [];
  }
  return [
    ["Summary", paper.summary_short],
    ["Simple summary", paper.summary_eli5],
    ["Abstract", paper.abstract],
  ].filter(([, value]) => Boolean(String(value ?? "").trim()));
}

function jobPayloadPreview(payload: Record<string, unknown>) {
  const serialized = JSON.stringify(payload, null, 2);
  return serialized && serialized !== "{}" ? serialized : "";
}

function paperSearchEntryFromPaper(paper: PaperResponse): AdminJobPaperResponse {
  return {
    root_job_id: "paper-search",
    job_id: "paper-search",
    paper_id: paper.paper_id,
    arxiv_id: paper.arxiv_id,
    title: paper.title,
    outcome: paper.index_status,
    skip_reason: null,
    created_at: paper.indexed_at ?? paper.updated ?? paper.published,
    updated_at: paper.indexed_at ?? paper.updated ?? paper.published,
    paper,
  };
}

function PaperFileTreeList({
  expandedFolders,
  onOpenFile,
  onToggleFolder,
  selectedFilePath,
  tree,
}: {
  expandedFolders: Record<string, boolean>;
  onOpenFile: (file: FileObject) => void;
  onToggleFolder: (path: string) => void;
  selectedFilePath: string | null;
  tree: FileTreeNode[];
}) {
  function fileTreeAsset(file: FileObject) {
    const contentType = (file.content_type ?? "").toLowerCase();
    if (isProbablyTextFile(file)) {
      return "code.svg";
    }
    if (contentType === "application/pdf") {
      return "docs.svg";
    }
    if (contentType.startsWith("image/")) {
      return "notes.svg";
    }
    return "docs.svg";
  }

  function renderNode(node: FileTreeNode, depth: number): ReactNode {
    const paddingLeft = 8 + depth * 12;
    if (node.kind === "folder") {
      const expanded = expandedFolders[node.path] ?? false;
      return (
        <div key={node.path} className="admin-paper-tree-folder">
          <button
            type="button"
            className="admin-paper-tree-folder-toggle"
            style={{ paddingLeft }}
            onClick={() => onToggleFolder(node.path)}
            aria-expanded={expanded}
          >
            <span className="admin-paper-tree-label">
              <AdminPaperIcon
                asset={expanded ? "project-opened.svg" : "project-closed.svg"}
                className="admin-paper-tree-icon"
              />
              <span>{node.name}</span>
            </span>
          </button>
          {expanded ? node.children.map((child) => renderNode(child, depth + 1)) : null}
        </div>
      );
    }
    return (
      <button
        key={node.path}
        type="button"
        className={`admin-paper-tree-file${selectedFilePath === node.path ? " active" : ""}`}
        style={{ paddingLeft }}
        onClick={() => onOpenFile(node.file)}
      >
        <span className="admin-paper-tree-label">
          <AdminPaperIcon asset={fileTreeAsset(node.file)} className="admin-paper-tree-icon" />
          <span>{node.name}</span>
        </span>
        <span className="admin-paper-tree-meta" aria-hidden="true">
          {node.file.content_type ?? "file"}
        </span>
      </button>
    );
  }

  return <div className="admin-paper-tree">{tree.map((node) => renderNode(node, 0))}</div>;
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
    const axisLabelInterval =
      timeline.buckets.length <= 16 ? 0 : Math.max(0, Math.ceil(timeline.buckets.length / 14) - 1);

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
      grid: { top: 8, right: 0, bottom: 26, left: 0 },
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
            `${formatWholeNumber(bucket.outcomes.updated)} updated`,
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
        axisLine: { show: true, lineStyle: { color: theme.line } },
        axisTick: {
          show: true,
          alignWithLabel: true,
          interval: 0,
          length: 3,
          lineStyle: { color: theme.line },
        },
        axisLabel: {
          color: theme.axisText,
          fontSize: 8,
          hideOverlap: true,
          show: true,
          margin: 4,
          alignMinLabel: "left",
          alignMaxLabel: "right",
          interval: axisLabelInterval,
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
      <strong>No tracked papers in this window</strong>
      <span>Pick another window, widen the range, or run a backfill job.</span>
    </>
  );

  return (
    <section className="admin-jobs-timeline-panel" aria-label="Tracked papers timeline">
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
  const [jobPapersOffset, setJobPapersOffset] = useState(0);
  const [jobPapersTotalCount, setJobPapersTotalCount] = useState(0);
  const [jobPapersHasMore, setJobPapersHasMore] = useState(false);
  const [jobPapersOutcomeCounts, setJobPapersOutcomeCounts] = useState<Record<string, number>>({});
  const [jobPapersSkippedReasonCounts, setJobPapersSkippedReasonCounts] = useState<
    Record<string, number>
  >({});

  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [createJobKind, setCreateJobKind] = useState<CreateJobKind | null>(null);
  const [createSubmitting, setCreateSubmitting] = useState(false);
  const createSubmittingRef = useRef(false);

  const [paperSearchOpen, setPaperSearchOpen] = useState(false);
  const [paperSearchQuery, setPaperSearchQuery] = useState("");
  const [paperSearchResults, setPaperSearchResults] = useState<PaperResponse[]>([]);
  const [paperSearchLoading, setPaperSearchLoading] = useState(false);
  const [paperSearchError, setPaperSearchError] = useState("");

  const [selectedJobPaper, setSelectedJobPaper] = useState<AdminJobPaperResponse | null>(null);
  const [paperModalTab, setPaperModalTab] = useState<PaperModalTab>("overview");
  const [paperDetail, setPaperDetail] = useState<PaperResponse | null>(null);
  const [paperJobTouches, setPaperJobTouches] = useState<AdminPaperJobTouchResponse[]>([]);
  const [paperCodeFiles, setPaperCodeFiles] = useState<CodeFileListResponse | null>(null);
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
  const [expandedFolders, setExpandedFolders] = useState<Record<string, boolean>>({});

  const activeCreateJob = useMemo(
    () => JOB_TYPE_OPTIONS.find((option) => option.kind === createJobKind) ?? null,
    [createJobKind],
  );
  const paperFileTree = useMemo(() => buildFileTree(paperFiles), [paperFiles]);
  const paperFolderCount = useMemo(() => countTreeFolders(paperFileTree), [paperFileTree]);
  const currentArxivId = paperArxivId(paperDetail, selectedJobPaper);
  const currentArxivUrl = paperArxivUrl(paperDetail, selectedJobPaper);
  const paperPreviewUrl = selectedJobPaper
    ? browserPaperPreviewContentUrl(selectedJobPaper.paper_id)
    : "";
  const selectedAssetPreviewUrl =
    selectedAsset && selectedJobPaper
      ? browserAdminPaperAssetContentUrl(selectedJobPaper.paper_id, selectedAsset.path)
      : "";

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

  const refreshJobPapers = useCallback(async (rootJobId: string, offset: number) => {
    setJobPapersLoading(true);
    setJobPapersError("");
    try {
      const response = await loadBrowserAdminJobPapers(
        rootJobId,
        offset,
        ADMIN_JOB_PAPERS_PAGE_SIZE,
      );
      setJobPapers(response.papers);
      setJobPapersOffset(response.offset);
      setJobPapersTotalCount(response.total_count);
      setJobPapersHasMore(response.has_more);
      setJobPapersOutcomeCounts(response.outcome_counts ?? {});
      setJobPapersSkippedReasonCounts(response.skipped_reason_counts ?? {});
    } catch (error) {
      setJobPapersError(panelErrorMessage(error, "Papers could not be loaded."));
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
      void refreshJobPapers(view.rootJobId, jobPapersOffset);
    }
  }, [
    jobPapersOffset,
    refreshJobPapers,
    view.kind,
    view.kind === "papers" ? view.rootJobId : null,
  ]);

  useEffect(() => {
    if (view.kind !== "papers") {
      return;
    }
    setJobPapersOffset(0);
    setJobPapers([]);
    setJobPapersTotalCount(0);
    setJobPapersHasMore(false);
    setJobPapersOutcomeCounts({});
    setJobPapersSkippedReasonCounts({});
    setSelectedJobPaper(null);
  }, [view.kind, view.kind === "papers" ? view.rootJobId : null]);

  useEffect(() => {
    if (!selectedJobPaper) {
      setPaperModalTab("overview");
      setPaperDetail(null);
      setPaperJobTouches([]);
      setPaperCodeFiles(null);
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
      setExpandedFolders({});
      return;
    }

    let cancelled = false;
    setPaperModalLoading(true);
    setPaperModalError("");
    setPaperDetail(selectedJobPaper.paper ?? null);
    setPaperJobTouches([]);
    setPaperCodeFiles(null);
    setPaperFiles([]);
    setPaperFilesError("");
    setSelectedFilePath(null);
    setSelectedFileContent(null);
    setSelectedAsset(null);
    setSelectedFileError("");
    setExpandedFolders({});

    void (async () => {
      try {
        const [paper, preview] = await Promise.all([
          getBrowserAdminPaperDetail(selectedJobPaper.paper_id).catch(() => null),
          getBrowserPaperPreview(selectedJobPaper.paper_id).catch(() => null),
        ]);
        if (cancelled) {
          return;
        }
        setPaperDetail(paper?.paper ?? selectedJobPaper.paper ?? null);
        setPaperJobTouches(paper?.jobs ?? []);
        setPaperCodeFiles(paper?.code_files ?? null);
        setPaperFiles(paper?.files ?? []);
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
    if (!selectedJobPaper || paperModalTab !== "files" || paperFiles.length > 0) {
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
  }, [paperFiles.length, paperModalTab, selectedJobPaper]);

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

  async function submitPaperSearch(formData: FormData) {
    const query = String(formData.get("paper_search_query") ?? "").trim();
    setPaperSearchQuery(query);
    setPaperSearchError("");
    setPaperSearchResults([]);
    if (!query) {
      setPaperSearchError("Enter a paper id, arXiv id, title, or author.");
      return;
    }
    setPaperSearchLoading(true);
    try {
      const results = await loadBrowserAdminPapers(query, "all", 25);
      setPaperSearchResults(results);
      if (results.length === 0) {
        setPaperSearchError("No papers matched that search.");
      }
    } catch (error) {
      setPaperSearchError(panelErrorMessage(error, "Paper search could not be loaded."));
    } finally {
      setPaperSearchLoading(false);
    }
  }

  function openPaperFromSearch(paper: PaperResponse) {
    setSelectedJobPaper(paperSearchEntryFromPaper(paper));
    setPaperModalTab("overview");
    setPaperSearchOpen(false);
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

  const jobPapersPageStart =
    jobPapersTotalCount === 0 || jobPapers.length === 0 ? 0 : jobPapersOffset + 1;
  const jobPapersPageEnd = jobPapersOffset + jobPapers.length;
  const jobPapersHasPrevious = jobPapersOffset > 0;
  const effectiveJobPapersOutcomeCounts =
    Object.keys(jobPapersOutcomeCounts).length > 0
      ? jobPapersOutcomeCounts
      : countJobPapersByOutcome(jobPapers);
  const effectiveJobPapersSkippedReasonCounts =
    Object.keys(jobPapersSkippedReasonCounts).length > 0
      ? jobPapersSkippedReasonCounts
      : countSkippedJobPapersByReason(jobPapers);
  const jobPaperStatusSegments = buildJobPaperStatusSegments(
    effectiveJobPapersOutcomeCounts,
    effectiveJobPapersSkippedReasonCounts,
  );
  const jobPaperStatusTotal = jobPaperStatusSegments.reduce(
    (sum, segment) => sum + segment.count,
    0,
  );
  const jobPaperStatusGradient = buildConicGradient(jobPaperStatusSegments);

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
              <button
                type="button"
                className="admin-compact-action admin-compact-action-window admin-jobs-search-button"
                onClick={() => {
                  setPaperSearchOpen(true);
                  setPaperSearchError("");
                }}
              >
                Search
              </button>
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
                    <th>Trigger</th>
                    <th>ID</th>
                    <th>Status</th>
                    <th>Papers</th>
                    <th>Updated</th>
                  </tr>
                </thead>
                <tbody data-testid={jobsLoading ? "admin-jobs-table-skeleton" : undefined}>
                  {jobsLoading ? (
                    <TableSkeletonRows columns={6} />
                  ) : filteredJobs.length === 0 ? (
                    <tr className="admin-jobs-empty-row">
                      <td colSpan={6}>
                        <div className="admin-jobs-empty">
                          No jobs in this window. Pick another window, widen the range, or create a
                          job.
                        </div>
                      </td>
                    </tr>
                  ) : (
                    filteredJobs.map((job) => {
                      const expanded = Boolean(expandedJobIds[job.job_id]);
                      const detailPairs = detailRows(job);
                      const trigger = adminJobTrigger(job);
                      const trackedPaperCount = trackedPaperCountForJob(job);
                      const toggleJobExpanded = () =>
                        setExpandedJobIds((current) => ({
                          ...current,
                          [job.job_id]: !current[job.job_id],
                        }));
                      return (
                        <FragmentRow key={job.job_id}>
                          <tr
                            className={`admin-jobs-row${expanded ? " expanded" : ""}`}
                            onClick={toggleJobExpanded}
                          >
                            <td>
                              <JobTypeCell
                                expanded={expanded}
                                job={job}
                                onToggle={toggleJobExpanded}
                              />
                            </td>
                            <td>
                              <span
                                className={`admin-jobs-trigger-tag trigger-${trigger.tone}`}
                                title={trigger.title}
                              >
                                {trigger.label}
                              </span>
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
                                  {trackedPaperCount ?? 0}
                                </button>
                              ) : (
                                <span>{trackedPaperCount ?? 0}</span>
                              )}
                            </td>
                            <td>{formatTimestamp(job.updated_at)}</td>
                          </tr>
                          {expanded ? (
                            <tr className="admin-jobs-expanded-row">
                              <td colSpan={6}>
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
                className="admin-jobs-icon-button"
                aria-label="Back to jobs"
                onClick={onBackToJobs}
              >
                ←
              </button>
              <h1 className="admin-jobs-paper-heading">
                Papers in <code>{view.rootJobId}</code>
              </h1>
            </div>
            <div className="admin-jobs-paper-meta">
              <span>
                {jobPapersLoading && jobPapersTotalCount === 0
                  ? "Loading papers"
                  : jobPapersTotalCount === 0
                    ? "0 papers"
                    : `${formatWholeNumber(jobPapersPageStart)}-${formatWholeNumber(
                        jobPapersPageEnd,
                      )} of ${formatWholeNumber(jobPapersTotalCount)}`}
              </span>
              <div className="admin-jobs-paper-pagination">
                <button
                  type="button"
                  className="admin-jobs-icon-button admin-jobs-paper-nav"
                  aria-label="Previous page"
                  disabled={jobPapersLoading || !jobPapersHasPrevious}
                  onClick={() => {
                    setSelectedJobPaper(null);
                    setJobPapersOffset((current) =>
                      Math.max(0, current - ADMIN_JOB_PAPERS_PAGE_SIZE),
                    );
                  }}
                >
                  ←
                </button>
                <button
                  type="button"
                  className="admin-jobs-icon-button admin-jobs-paper-nav"
                  aria-label="Next page"
                  disabled={jobPapersLoading || !jobPapersHasMore}
                  onClick={() => {
                    setSelectedJobPaper(null);
                    setJobPapersOffset((current) => current + ADMIN_JOB_PAPERS_PAGE_SIZE);
                  }}
                >
                  →
                </button>
              </div>
            </div>
          </div>

          {jobPapersError ? <div className="admin-jobs-inline-error">{jobPapersError}</div> : null}
          <div className="admin-jobs-surface">
            {jobPapersLoading && jobPapersTotalCount === 0 ? (
              <div className="admin-jobs-empty">Loading papers...</div>
            ) : null}
            {jobPaperStatusTotal > 0 ? (
              <div
                className="admin-paper-status-summary"
                role="group"
                aria-label="Tracked paper outcomes"
              >
                <div
                  className="admin-paper-status-donut"
                  style={{ background: jobPaperStatusGradient }}
                  aria-hidden="true"
                />
                <div className="admin-paper-status-legend">
                  {jobPaperStatusSegments.map((segment) => (
                    <span key={segment.label} className="admin-paper-status-legend-item">
                      <span
                        className="admin-paper-status-swatch"
                        style={{ backgroundColor: segment.color }}
                        aria-hidden="true"
                      />
                      <span>{segment.label}</span>
                      <strong>{formatWholeNumber(segment.count)}</strong>
                    </span>
                  ))}
                </div>
              </div>
            ) : null}
            {!jobPapersLoading || jobPapers.length > 0 || jobPapersTotalCount === 0 ? (
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
                            <div
                              className="admin-paper-primary"
                              title={paper.title ?? paper.paper_id}
                            >
                              {truncateText(paper.title ?? paper.paper_id, 60)}
                            </div>
                            <div className="admin-paper-secondary">
                              {paper.arxiv_id ?? paper.paper_id}
                            </div>
                          </td>
                          <td>
                            <span
                              className={`admin-jobs-status status-${paper.outcome}`}
                              title={
                                paper.outcome === "skipped"
                                  ? `Skipped: ${humanizeReason(paper.skip_reason)}`
                                  : undefined
                              }
                            >
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

      {paperSearchOpen ? (
        <div
          className="modal"
          role="dialog"
          aria-modal="true"
          aria-labelledby="admin-paper-search-title"
        >
          <div
            className="modal-backdrop"
            onClick={() => {
              if (!paperSearchLoading) {
                setPaperSearchOpen(false);
              }
            }}
          />
          <div className="modal-panel admin-paper-search-modal">
            <button
              type="button"
              className="modal-close"
              aria-label="Close paper search"
              onClick={() => setPaperSearchOpen(false)}
            >
              ×
            </button>
            <h2 id="admin-paper-search-title">Search papers</h2>
            <form
              className="admin-paper-search-form"
              aria-busy={paperSearchLoading}
              onSubmit={async (event) => {
                event.preventDefault();
                await submitPaperSearch(new FormData(event.currentTarget));
              }}
            >
              <input
                name="paper_search_query"
                defaultValue={paperSearchQuery}
                placeholder="Paper id, title, or arXiv id"
                autoFocus
              />
              <button
                type="submit"
                className="admin-compact-action admin-compact-action-primary"
                disabled={paperSearchLoading}
              >
                {paperSearchLoading ? "Searching..." : "Search"}
              </button>
            </form>
            {paperSearchError ? (
              <div className="admin-jobs-inline-error">{paperSearchError}</div>
            ) : null}
            <div className="admin-paper-search-results">
              {paperSearchLoading ? (
                <div className="admin-jobs-empty">Searching papers...</div>
              ) : null}
              {!paperSearchLoading && paperSearchResults.length > 0
                ? paperSearchResults.map((paper) => (
                    <button
                      key={paper.paper_id}
                      type="button"
                      className="admin-paper-search-result"
                      onClick={() => openPaperFromSearch(paper)}
                    >
                      <span className="admin-paper-search-result-main">
                        <strong>{paper.title || paper.paper_id}</strong>
                        <span>
                          {paper.arxiv_id || paper.paper_id} · {paper.authors || "Unknown authors"}
                        </span>
                      </span>
                      <span className={`admin-jobs-status status-${paper.index_status}`}>
                        {statusLabel(paper.index_status)}
                      </span>
                    </button>
                  ))
                : null}
              {!paperSearchLoading && !paperSearchError && paperSearchResults.length === 0 ? (
                <div className="admin-jobs-empty">
                  Search by paper id, arXiv id, title, or author.
                </div>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}

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
              <div className="admin-paper-modal-header-links">
                {currentArxivUrl ? (
                  <a href={currentArxivUrl} target="_blank" rel="noreferrer">
                    {currentArxivId ? `Open arXiv ${currentArxivId}` : "Open paper"}
                  </a>
                ) : null}
                {paperDetail?.pdf_url ? (
                  <a href={paperDetail.pdf_url} target="_blank" rel="noreferrer">
                    PDF
                  </a>
                ) : null}
              </div>
            </div>
            <div className="admin-paper-modal-divider" />
            <div className="admin-paper-modal-layout">
              <div className="admin-paper-modal-sidebar">
                <div
                  className="admin-paper-modal-tabs"
                  role="tablist"
                  aria-label="Paper detail views"
                  aria-orientation="vertical"
                >
                  {PAPER_MODAL_TABS.map((tab) => (
                    <button
                      key={tab.id}
                      type="button"
                      role="tab"
                      aria-selected={paperModalTab === tab.id}
                      className={`admin-paper-tab${paperModalTab === tab.id ? " active" : ""}`}
                      onClick={() => setPaperModalTab(tab.id)}
                    >
                      <AdminPaperIcon asset={tab.asset} className="admin-paper-tab-icon" />
                      <span>{tab.label}</span>
                    </button>
                  ))}
                </div>
                <div className="admin-paper-sidebar-card">
                  <span className="admin-paper-sidebar-label">Quick facts</span>
                  <div className="admin-paper-sidebar-facts">
                    <div>
                      <span>Status</span>
                      <strong>{paperDetail?.index_status ?? selectedJobPaper.outcome}</strong>
                    </div>
                    <div>
                      <span>Published</span>
                      <strong>{paperDetail?.published ?? "—"}</strong>
                    </div>
                    <div>
                      <span>Preview</span>
                      <strong>{paperPreview ? "Available" : "Missing"}</strong>
                    </div>
                    <div>
                      <span>Files</span>
                      <strong>
                        {paperFiles.length > 0
                          ? `${paperFiles.length} files`
                          : !paperFilesLoading
                            ? "0 files"
                            : "—"}
                      </strong>
                    </div>
                    <div>
                      <span>Folders</span>
                      <strong>
                        {paperFiles.length > 0
                          ? `${paperFolderCount} folders`
                          : !paperFilesLoading
                            ? "0 folders"
                            : "—"}
                      </strong>
                    </div>
                    <div>
                      <span>Jobs</span>
                      <strong>{paperJobTouches.length}</strong>
                    </div>
                    <div>
                      <span>Code</span>
                      <strong>
                        {paperCodeFiles?.eligibility.eligible
                          ? `${paperCodeFiles.files.length} files`
                          : paperDetail?.code_url
                            ? "Linked"
                            : "—"}
                      </strong>
                    </div>
                  </div>
                </div>
                <div className="admin-paper-sidebar-card">
                  <span className="admin-paper-sidebar-label">Links</span>
                  <div className="admin-paper-sidebar-links">
                    {currentArxivUrl ? (
                      <a href={currentArxivUrl} target="_blank" rel="noreferrer">
                        arXiv page
                      </a>
                    ) : null}
                    {paperDetail?.pdf_url ? (
                      <a href={paperDetail.pdf_url} target="_blank" rel="noreferrer">
                        PDF
                      </a>
                    ) : null}
                    {paperDetail?.html_url && paperDetail.html_url !== currentArxivUrl ? (
                      <a href={paperDetail.html_url} target="_blank" rel="noreferrer">
                        Publisher page
                      </a>
                    ) : null}
                    {paperDetail?.code_url ? (
                      <a href={paperDetail.code_url} target="_blank" rel="noreferrer">
                        Code
                      </a>
                    ) : null}
                  </div>
                </div>
              </div>
              <div className="admin-paper-modal-main">
                {paperModalError ? (
                  <div className="admin-jobs-inline-error">{paperModalError}</div>
                ) : null}
                {paperModalLoading ? (
                  <div className="admin-jobs-empty">Loading paper...</div>
                ) : null}

                {!paperModalLoading && paperModalTab === "overview" ? (
                  <div className="admin-paper-modal-body">
                    <div className="admin-paper-section-header">
                      <h3>Overview</h3>
                      <span>Summary, identifiers, code, and job activity</span>
                    </div>
                    <div className="admin-paper-overview-grid">
                      <div className="admin-paper-overview-card">
                        <h4>Summaries</h4>
                        {paperSummaryBlocks(paperDetail).length > 0 ? (
                          paperSummaryBlocks(paperDetail).map(([label, value]) => (
                            <section key={label} className="admin-paper-summary-block">
                              <span>{label}</span>
                              <p>{value}</p>
                            </section>
                          ))
                        ) : (
                          <div className="admin-jobs-empty">No summary or abstract is stored.</div>
                        )}
                      </div>
                      <div className="admin-paper-overview-card">
                        <h4>Paper</h4>
                        <div className="admin-paper-metadata compact">
                          {[
                            ["Paper id", paperDetail?.paper_id ?? selectedJobPaper.paper_id],
                            ["arXiv id", paperDetail?.arxiv_id ?? selectedJobPaper.arxiv_id ?? "—"],
                            ["Status", paperDetail?.index_status ?? selectedJobPaper.outcome],
                            ["Citations", paperDetail ? String(paperDetail.citation_count) : "—"],
                            ["Indexed at", paperDetail?.indexed_at ?? "—"],
                          ].map(([label, value]) => (
                            <div key={label} className="admin-paper-metadata-row">
                              <span>{label}</span>
                              <strong>{value}</strong>
                            </div>
                          ))}
                        </div>
                      </div>
                      <div className="admin-paper-overview-card">
                        <h4>Code</h4>
                        <div className="admin-paper-code-summary">
                          {paperDetail?.code_url ? (
                            <a href={paperDetail.code_url} target="_blank" rel="noreferrer">
                              {paperDetail.code_url}
                            </a>
                          ) : (
                            <span>No code URL stored.</span>
                          )}
                          <span>Status: {paperDetail?.code_index_status ?? "none"}</span>
                          {paperCodeFiles?.resolved_sha ? (
                            <span>Resolved SHA: {paperCodeFiles.resolved_sha}</span>
                          ) : null}
                          {paperCodeFiles?.eligibility.reason ? (
                            <span>{paperCodeFiles.eligibility.reason}</span>
                          ) : null}
                        </div>
                      </div>
                      <div className="admin-paper-overview-card">
                        <h4>Jobs</h4>
                        {paperJobTouches.length > 0 ? (
                          <div className="admin-paper-job-mini-list">
                            {paperJobTouches.slice(0, 5).map((job) => (
                              <div key={`${job.job_id}:${job.record_updated_at}`}>
                                <strong>{JOB_TYPE_LABELS[job.job_type] ?? job.job_type}</strong>
                                <span>
                                  {statusLabel(job.outcome)} ·{" "}
                                  {formatTimestamp(job.record_updated_at)}
                                </span>
                              </div>
                            ))}
                          </div>
                        ) : (
                          <div className="admin-jobs-empty">No tracked jobs for this paper.</div>
                        )}
                      </div>
                    </div>
                  </div>
                ) : null}

                {!paperModalLoading && paperModalTab === "screenshot" ? (
                  <div className="admin-paper-modal-body">
                    <div className="admin-paper-section-header">
                      <h3>First-page screenshot</h3>
                      <span>Stored preview</span>
                    </div>
                    {paperPreview ? (
                      <div className="admin-paper-preview-stage">
                        <img
                          className="admin-paper-preview"
                          src={paperPreviewUrl}
                          alt={paperDetail?.title ?? selectedJobPaper.paper_id}
                        />
                      </div>
                    ) : (
                      <div className="admin-jobs-empty">
                        No screenshot available for this paper.
                      </div>
                    )}
                  </div>
                ) : null}

                {!paperModalLoading && paperModalTab === "metadata" ? (
                  <div className="admin-paper-modal-body">
                    <div className="admin-paper-section-header">
                      <h3>Metadata</h3>
                      <span>Full paper record</span>
                    </div>
                    <div className="admin-paper-metadata">
                      {metadataRows(paperDetail, selectedJobPaper).map(([label, value]) => (
                        <div key={label} className="admin-paper-metadata-row">
                          <span>{label}</span>
                          <strong>{value}</strong>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}

                {!paperModalLoading && paperModalTab === "jobs" ? (
                  <div className="admin-paper-modal-body">
                    <div className="admin-paper-section-header">
                      <h3>Jobs</h3>
                      <span>{paperJobTouches.length} tracked touches</span>
                    </div>
                    {paperJobTouches.length === 0 ? (
                      <div className="admin-jobs-empty">No tracked jobs for this paper.</div>
                    ) : (
                      <div className="admin-paper-jobs-list">
                        {paperJobTouches.map((job) => (
                          <article key={`${job.job_id}:${job.record_updated_at}`}>
                            <div className="admin-paper-job-row-header">
                              <div>
                                <strong>{JOB_TYPE_LABELS[job.job_type] ?? job.job_type}</strong>
                                <code>{job.job_id}</code>
                              </div>
                              <span className={`admin-jobs-status status-${job.display_status}`}>
                                {statusLabel(job.display_status)}
                              </span>
                            </div>
                            <div className="admin-paper-job-row-meta">
                              <span>Outcome: {statusLabel(job.outcome)}</span>
                              {job.skip_reason ? (
                                <span>Skip reason: {humanizeReason(job.skip_reason)}</span>
                              ) : null}
                              <span>Root: {job.root_job_id}</span>
                              <span>Updated: {formatTimestamp(job.record_updated_at)}</span>
                              <span>
                                Attempts: {job.attempts}/{job.max_attempts}
                              </span>
                            </div>
                            {job.error ? (
                              <div className="admin-jobs-inline-error">{job.error}</div>
                            ) : null}
                            {jobPayloadPreview(job.payload) ? (
                              <pre className="admin-paper-job-payload">
                                {jobPayloadPreview(job.payload)}
                              </pre>
                            ) : null}
                          </article>
                        ))}
                      </div>
                    )}
                  </div>
                ) : null}

                {!paperModalLoading && paperModalTab === "code" ? (
                  <div className="admin-paper-modal-body">
                    <div className="admin-paper-section-header">
                      <h3>Code</h3>
                      <span>
                        {paperCodeFiles?.eligibility.eligible
                          ? `${paperCodeFiles.files.length} indexed repo files`
                          : "Repository metadata"}
                      </span>
                    </div>
                    <div className="admin-paper-code-summary">
                      {paperDetail?.code_url ? (
                        <a href={paperDetail.code_url} target="_blank" rel="noreferrer">
                          {paperDetail.code_url}
                        </a>
                      ) : (
                        <span>No code URL stored.</span>
                      )}
                      <span>Status: {paperDetail?.code_index_status ?? "none"}</span>
                      <span>
                        Python:{" "}
                        {paperDetail?.code_is_python === true
                          ? "yes"
                          : paperDetail?.code_is_python === false
                            ? "no"
                            : "unknown"}
                      </span>
                      {paperDetail?.code_python_reason ? (
                        <span>{paperDetail.code_python_reason}</span>
                      ) : null}
                      {paperCodeFiles?.resolved_sha ? (
                        <span>Resolved SHA: {paperCodeFiles.resolved_sha}</span>
                      ) : null}
                    </div>
                    {paperCodeFiles?.files.length ? (
                      <div className="admin-paper-code-files">
                        {paperCodeFiles.files.map((file) => (
                          <div key={file.repo_path} className="admin-paper-code-file">
                            <code>{file.repo_path}</code>
                            <span>
                              {formatWholeNumber(file.size_bytes)} bytes ·{" "}
                              {file.indexed ? "indexed" : "stored"}
                            </span>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="admin-jobs-empty">No code files are available.</div>
                    )}
                  </div>
                ) : null}

                {!paperModalLoading && paperModalTab === "files" ? (
                  <div className="admin-paper-modal-body">
                    <div className="admin-paper-section-header">
                      <h3>File structure</h3>
                      <span>
                        {paperFiles.length > 0
                          ? `${paperFiles.length} files across ${paperFolderCount} folders`
                          : "Browse stored artifacts"}
                      </span>
                    </div>
                    <div className="admin-paper-files">
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
                        {!paperFilesLoading && paperFiles.length > 0 ? (
                          <PaperFileTreeList
                            expandedFolders={expandedFolders}
                            onOpenFile={(file) => void openFile(file)}
                            onToggleFolder={(path) =>
                              setExpandedFolders((current) => ({
                                ...current,
                                [path]: !(current[path] ?? false),
                              }))
                            }
                            selectedFilePath={selectedFilePath}
                            tree={paperFileTree}
                          />
                        ) : null}
                      </div>
                      <div className="admin-paper-file-preview">
                        <div className="admin-paper-file-preview-header">
                          <strong>{selectedFilePath ?? "File preview"}</strong>
                          {selectedAsset && selectedAssetPreviewUrl ? (
                            <a
                              href={selectedAssetPreviewUrl}
                              target="_blank"
                              rel="noreferrer"
                              className="admin-paper-file-link"
                            >
                              Open raw file
                            </a>
                          ) : null}
                        </div>
                        <div
                          className={`admin-paper-file-preview-body${selectedFileContent ? " text-mode" : ""}`}
                        >
                          {selectedFileError ? (
                            <div className="admin-jobs-inline-error">{selectedFileError}</div>
                          ) : null}
                          {selectedFileLoading ? (
                            <div className="admin-jobs-empty">Opening file...</div>
                          ) : null}
                          {!selectedFileLoading && selectedFileContent ? (
                            <pre className="admin-paper-file-text">
                              {selectedFileContent.content}
                            </pre>
                          ) : null}
                          {!selectedFileLoading &&
                          selectedAsset &&
                          assetKind(selectedAsset) === "image" ? (
                            <div className="admin-paper-preview-stage">
                              <img
                                className="admin-paper-preview"
                                src={selectedAssetPreviewUrl}
                                alt={selectedAsset.path}
                              />
                            </div>
                          ) : null}
                          {!selectedFileLoading &&
                          selectedAsset &&
                          assetKind(selectedAsset) === "pdf" ? (
                            <iframe
                              className="admin-paper-pdf-frame"
                              src={selectedAssetPreviewUrl}
                              title={selectedAsset.path}
                            />
                          ) : null}
                          {!selectedFileLoading &&
                          selectedAsset &&
                          assetKind(selectedAsset) === "file" ? (
                            <a
                              href={selectedAssetPreviewUrl}
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
                    </div>
                  </div>
                ) : null}
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}

function FragmentRow({ children }: { children: ReactNode }) {
  return <>{children}</>;
}
