import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TrendingPapersPage, type TrendingBrowseState } from "./TrendingPapersPage";

const apiMocks = vi.hoisted(() => ({
  createBrowserTrendingPaperComment: vi.fn(),
  getBrowserTrendingPaper: vi.fn(),
  loadBrowserTrendingPapers: vi.fn(),
  removeUpvoteBrowserTrendingPaper: vi.fn(),
  upvoteBrowserTrendingPaper: vi.fn(),
}));

vi.mock("./api", () => ({
  createBrowserTrendingPaperComment: apiMocks.createBrowserTrendingPaperComment,
  getBrowserTrendingPaper: apiMocks.getBrowserTrendingPaper,
  loadBrowserTrendingPapers: apiMocks.loadBrowserTrendingPapers,
  removeUpvoteBrowserTrendingPaper: apiMocks.removeUpvoteBrowserTrendingPaper,
  upvoteBrowserTrendingPaper: apiMocks.upvoteBrowserTrendingPaper,
}));

function Harness({
  authenticated = true,
  initialState = {
    date: "2026-04-21",
    sort: "default",
    paperId: null,
  } satisfies TrendingBrowseState,
  onRequireAuth = vi.fn(),
}: {
  authenticated?: boolean;
  initialState?: TrendingBrowseState;
  onRequireAuth?: () => void;
}) {
  const [browseState, setBrowseState] = useState<TrendingBrowseState>(initialState);
  return (
    <TrendingPapersPage
      variant="workspace"
      authenticated={authenticated}
      browseState={browseState}
      onChangeBrowseState={setBrowseState}
      onRequireAuth={onRequireAuth}
    />
  );
}

describe("TrendingPapersPage", () => {
  beforeEach(() => {
    apiMocks.loadBrowserTrendingPapers.mockResolvedValue({
      date: "2026-04-21",
      sort: "default",
      papers: [
        {
          paper_id: "arxiv:2401.12345",
          arxiv_id: "2401.12345",
          title: "Trending Paper",
          authors: "Ada Lovelace",
          published: "2026-04-21",
          html_url: "https://example.test/2401.12345",
          pdf_url: "https://example.test/2401.12345.pdf",
          image_url: "https://example.test/preview.webp",
          upvote_count: 12,
          viewer_has_upvoted: false,
        },
      ],
    });
    apiMocks.getBrowserTrendingPaper.mockResolvedValue({
      paper_id: "arxiv:2401.12345",
      arxiv_id: "2401.12345",
      title: "Trending Paper",
      authors: "Ada Lovelace",
      published: "2026-04-21",
      primary_category: "cs.AI",
      categories: "cs.AI",
      summary_eli5: "Simple summary",
      html_url: "https://example.test/2401.12345",
      pdf_url: "https://example.test/2401.12345.pdf",
      image_url: "https://example.test/preview.webp",
      upvote_count: 12,
      viewer_has_upvoted: false,
      comments: [
        {
          comment_id: "pcmt_1",
          paper_id: "arxiv:2401.12345",
          parent_comment_id: null,
          author: { user_id: "user_1", display_name: "Ada" },
          body: "Root note",
          created_at: "2026-04-21T00:00:00Z",
          updated_at: "2026-04-21T00:00:00Z",
          replies: [
            {
              comment_id: "pcmt_2",
              paper_id: "arxiv:2401.12345",
              parent_comment_id: "pcmt_1",
              author: { user_id: "user_2", display_name: "Grace" },
              body: "Reply note",
              created_at: "2026-04-21T01:00:00Z",
              updated_at: "2026-04-21T01:00:00Z",
              replies: [],
            },
          ],
        },
      ],
    });
    apiMocks.upvoteBrowserTrendingPaper.mockResolvedValue({
      paper_id: "arxiv:2401.12345",
      upvote_count: 13,
      viewer_has_upvoted: true,
    });
    apiMocks.removeUpvoteBrowserTrendingPaper.mockResolvedValue({
      paper_id: "arxiv:2401.12345",
      upvote_count: 12,
      viewer_has_upvoted: false,
    });
    apiMocks.createBrowserTrendingPaperComment.mockResolvedValue({
      paper_id: "arxiv:2401.12345",
      comments: [],
    });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders the feed, re-sorts it, and opens the detail view with nested comments", async () => {
    render(<Harness />);

    await waitFor(() => {
      expect(screen.getByText("Trending Paper")).toBeTruthy();
    });
    expect(apiMocks.loadBrowserTrendingPapers).toHaveBeenCalledWith("2026-04-21", "default");

    fireEvent.click(screen.getByRole("tab", { name: "Hottest" }));

    await waitFor(() => {
      expect(apiMocks.loadBrowserTrendingPapers).toHaveBeenLastCalledWith("2026-04-21", "upvotes");
    });

    fireEvent.click(screen.getByText("Trending Paper"));

    await waitFor(() => {
      expect(screen.getByText("ELI5 summary")).toBeTruthy();
    });
    expect(screen.getByText("Simple summary")).toBeTruthy();
    expect(screen.getByText("Root note")).toBeTruthy();
    expect(screen.getByText("Reply note")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Read paper" }).getAttribute("href")).toBe(
      "https://example.test/2401.12345",
    );
    expect(screen.getByRole("link", { name: "PDF" }).getAttribute("href")).toBe(
      "https://example.test/2401.12345.pdf",
    );
  });

  it("shows the empty state when the selected day has no papers", async () => {
    apiMocks.loadBrowserTrendingPapers.mockResolvedValueOnce({
      date: "2026-04-21",
      sort: "default",
      papers: [],
    });

    render(<Harness />);

    await waitFor(() => {
      expect(screen.getByText(/No indexed papers were published on/)).toBeTruthy();
    });
  });

  it("routes unauthenticated vote attempts to the auth callback", async () => {
    const onRequireAuth = vi.fn();
    render(<Harness authenticated={false} onRequireAuth={onRequireAuth} />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Upvote 12" })).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: "Upvote 12" }));

    expect(onRequireAuth).toHaveBeenCalledTimes(1);
    expect(apiMocks.upvoteBrowserTrendingPaper).not.toHaveBeenCalled();
  });
});
