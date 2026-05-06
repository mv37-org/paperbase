import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AdminJobOverviewRow } from "./types";

const apiMocks = vi.hoisted(() => ({
  browserAdminPaperAssetContentUrl: vi.fn(
    (paperId: string, path: string) =>
      `/v1/browser/admin/papers/${encodeURIComponent(paperId)}/asset-content/${path}`,
  ),
  browserPaperPreviewContentUrl: vi.fn(
    (paperId: string) => `/v1/browser/papers/${encodeURIComponent(paperId)}/preview/content`,
  ),
  getBrowserAdminPaperAsset: vi.fn(),
  getBrowserAdminPaperDetail: vi.fn(),
  getBrowserAdminPaperFile: vi.fn(),
  getBrowserPaper: vi.fn(),
  getBrowserPaperPreview: vi.fn(),
  loadBrowserAdminJobPapers: vi.fn(),
  loadBrowserAdminJobsOverview: vi.fn(),
  loadBrowserAdminPaperFiles: vi.fn(),
  loadBrowserAdminPapers: vi.fn(),
  queueBrowserAdminEnrichMissingMetadata: vi.fn(),
  queueBrowserAdminHarvestRange: vi.fn(),
  queueBrowserAdminHarvestTopCited: vi.fn(),
  queueBrowserAdminHarvestVenue: vi.fn(),
  queueBrowserAdminPaperIndex: vi.fn(),
}));

vi.mock("./api", () => ({
  browserAdminPaperAssetContentUrl: apiMocks.browserAdminPaperAssetContentUrl,
  browserPaperPreviewContentUrl: apiMocks.browserPaperPreviewContentUrl,
  getBrowserAdminPaperAsset: apiMocks.getBrowserAdminPaperAsset,
  getBrowserAdminPaperDetail: apiMocks.getBrowserAdminPaperDetail,
  getBrowserAdminPaperFile: apiMocks.getBrowserAdminPaperFile,
  getBrowserPaper: apiMocks.getBrowserPaper,
  getBrowserPaperPreview: apiMocks.getBrowserPaperPreview,
  loadBrowserAdminJobPapers: apiMocks.loadBrowserAdminJobPapers,
  loadBrowserAdminJobsOverview: apiMocks.loadBrowserAdminJobsOverview,
  loadBrowserAdminPaperFiles: apiMocks.loadBrowserAdminPaperFiles,
  loadBrowserAdminPapers: apiMocks.loadBrowserAdminPapers,
  queueBrowserAdminEnrichMissingMetadata: apiMocks.queueBrowserAdminEnrichMissingMetadata,
  queueBrowserAdminHarvestRange: apiMocks.queueBrowserAdminHarvestRange,
  queueBrowserAdminHarvestTopCited: apiMocks.queueBrowserAdminHarvestTopCited,
  queueBrowserAdminHarvestVenue: apiMocks.queueBrowserAdminHarvestVenue,
  queueBrowserAdminPaperIndex: apiMocks.queueBrowserAdminPaperIndex,
}));

vi.mock("echarts-for-react", () => ({
  default: () => <div data-testid="admin-jobs-chart-inner" />,
}));

import { AdminOpsPanel } from "./AdminOpsPanel";

function adminJob(overrides: Partial<AdminJobOverviewRow> = {}): AdminJobOverviewRow {
  return {
    job_id: "job_summary_1",
    root_job_id: "job_summary_1",
    job_type: "summarize_top_cited",
    status: "completed",
    display_status: "completed",
    payload: {},
    attempts: 1,
    max_attempts: 2,
    priority: 0,
    created_at: "2026-05-06T08:00:00Z",
    updated_at: "2026-05-06T09:00:00Z",
    is_synthetic: false,
    tracking_available: true,
    indexed_paper_count: 0,
    tracked_paper_count: 1,
    paper_outcome_counts: { updated: 1 },
    latest_status_summary: "1 completed · 1 total",
    child_job_count: 0,
    details: {},
    ...overrides,
  };
}

function renderAdminOpsPanel() {
  return render(
    <AdminOpsPanel
      view={{ kind: "jobs" }}
      onBackToJobs={() => undefined}
      onError={() => undefined}
      onNote={() => undefined}
      onOpenJobPapers={() => undefined}
    />,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

describe("AdminOpsPanel", () => {
  it("shows job type descriptions from info tooltips without expanding the row", async () => {
    vi.spyOn(Date, "now").mockReturnValue(new Date("2026-05-06T12:00:00Z").getTime());
    apiMocks.loadBrowserAdminJobsOverview.mockResolvedValue({
      jobs: [adminJob()],
      worker: null,
    });

    renderAdminOpsPanel();

    const describeButton = await screen.findByRole("button", {
      name: "Describe Summarize top cited",
    });
    expect(describeButton.textContent).toBe("i");
    expect(describeButton.getAttribute("title")).toBe(
      "Summarize already-indexed high-citation papers.",
    );
    expect(screen.getByRole("tooltip").textContent).toBe(
      "Summarize already-indexed high-citation papers.",
    );

    fireEvent.click(describeButton);

    await waitFor(() => {
      expect(apiMocks.loadBrowserAdminJobsOverview).toHaveBeenCalledWith(200);
    });
    expect(screen.queryByText("Latest status: 1 completed · 1 total")).toBeNull();
  });

  it("keeps the latest sync schedule row visible even when its server timestamp is ahead", async () => {
    vi.spyOn(Date, "now").mockReturnValue(new Date("2026-05-06T23:00:00Z").getTime());
    apiMocks.loadBrowserAdminJobsOverview.mockResolvedValue({
      jobs: [
        adminJob({
          job_id: "env:latest_sync",
          root_job_id: "env:latest_sync",
          job_type: "latest_sync_schedule",
          status: "scheduled",
          display_status: "scheduled",
          attempts: 0,
          max_attempts: 0,
          created_at: "2026-05-06T23:05:28.903908Z",
          updated_at: "2026-05-06T23:05:28.903908Z",
          is_synthetic: true,
          tracking_available: false,
          indexed_paper_count: null,
          tracked_paper_count: null,
          paper_outcome_counts: {},
          latest_status_summary: "Runs on the worker schedule.",
          details: {
            next_expected_run_at: "2026-05-06T23:05:28.903908Z",
          },
        }),
      ],
      worker: null,
    });

    renderAdminOpsPanel();

    expect(await screen.findByText("env:latest_sync")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Describe Latest sync" })).toBeTruthy();
    expect(screen.queryByTestId("admin-jobs-table-skeleton")).toBeNull();
    expect(screen.queryByText("No jobs in this window.")).toBeNull();
  });

  it("searches papers from the admin header and opens full paper details", async () => {
    apiMocks.loadBrowserAdminJobsOverview.mockResolvedValue({
      jobs: [adminJob()],
      worker: null,
    });
    const paper = {
      paper_id: "arxiv:2401.54321",
      arxiv_id: "2401.54321",
      title: "Searchable Benchmark Paper",
      authors: "Ada, Grace",
      abstract: "A full abstract.",
      summary_short: "A technical summary.",
      summary_eli5: "A simple summary.",
      categories: "cs.AI",
      primary_category: "cs.AI",
      published: "2026-04-21",
      updated: "2026-04-22",
      pdf_url: "https://example.test/paper.pdf",
      html_url: "https://example.test/paper",
      citation_count: 12,
      influential_citation_count: 3,
      code_url: "https://github.com/example/repo",
      code_index_status: "indexed",
      index_status: "indexed",
    };
    apiMocks.loadBrowserAdminPapers.mockResolvedValue([paper]);
    apiMocks.getBrowserAdminPaperDetail.mockResolvedValue({
      paper,
      jobs: [
        {
          root_job_id: "job_root_1",
          job_id: "job_chunk_1",
          job_type: "harvest_chunk",
          status: "completed",
          display_status: "completed",
          payload: { arxiv_id: "2401.54321" },
          outcome: "indexed",
          attempts: 1,
          max_attempts: 3,
          priority: 0,
          record_created_at: "2026-04-21T00:00:00Z",
          record_updated_at: "2026-04-21T00:00:00Z",
          created_at: "2026-04-21T00:00:00Z",
          updated_at: "2026-04-21T00:00:00Z",
        },
      ],
      files: [{ path: "text/full.md", size: 10, content_type: "text/markdown" }],
      code_files: {
        paper_id: "arxiv:2401.54321",
        eligibility: { eligible: true, status: "indexed" },
        resolved_sha: "abc123",
        files: [{ repo_path: "README.md", size_bytes: 10, indexed: true }],
      },
    });
    apiMocks.getBrowserPaperPreview.mockResolvedValue(null);

    renderAdminOpsPanel();

    fireEvent.click(await screen.findByRole("button", { name: "Search" }));
    fireEvent.change(screen.getByPlaceholderText("Paper id, title, or arXiv id"), {
      target: { value: "2401.54321" },
    });
    fireEvent.click(screen.getAllByRole("button", { name: "Search" }).at(-1)!);

    await waitFor(() => {
      expect(apiMocks.loadBrowserAdminPapers).toHaveBeenCalledWith("2401.54321", "all", 25);
    });
    fireEvent.click(await screen.findByText("Searchable Benchmark Paper"));

    expect(await screen.findByText("A technical summary.")).toBeTruthy();
    expect(screen.getByText("https://github.com/example/repo")).toBeTruthy();
    expect(screen.getAllByText("Jobs").length).toBeGreaterThan(0);
    await waitFor(() => {
      expect(apiMocks.getBrowserAdminPaperDetail).toHaveBeenCalledWith("arxiv:2401.54321");
    });
  });
});
