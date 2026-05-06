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
  getBrowserAdminPaperFile: vi.fn(),
  getBrowserPaper: vi.fn(),
  getBrowserPaperPreview: vi.fn(),
  loadBrowserAdminJobPapers: vi.fn(),
  loadBrowserAdminJobsOverview: vi.fn(),
  loadBrowserAdminPaperFiles: vi.fn(),
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
  getBrowserAdminPaperFile: apiMocks.getBrowserAdminPaperFile,
  getBrowserPaper: apiMocks.getBrowserPaper,
  getBrowserPaperPreview: apiMocks.getBrowserPaperPreview,
  loadBrowserAdminJobPapers: apiMocks.loadBrowserAdminJobPapers,
  loadBrowserAdminJobsOverview: apiMocks.loadBrowserAdminJobsOverview,
  loadBrowserAdminPaperFiles: apiMocks.loadBrowserAdminPaperFiles,
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
});
