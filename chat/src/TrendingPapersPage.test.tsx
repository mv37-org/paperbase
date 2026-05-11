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
    page: 1,
    paperId: null,
  } satisfies TrendingBrowseState,
  onOpenPaper,
  onRequireAuth = vi.fn(),
  variant = "workspace",
}: {
  authenticated?: boolean;
  initialState?: TrendingBrowseState;
  onOpenPaper?: (nextState: TrendingBrowseState) => void;
  onRequireAuth?: () => void;
  variant?: "landing" | "public" | "workspace";
}) {
  const [browseState, setBrowseState] = useState<TrendingBrowseState>(initialState);
  return (
    <TrendingPapersPage
      variant={variant}
      authenticated={authenticated}
      browseState={browseState}
      onChangeBrowseState={setBrowseState}
      onOpenPaper={onOpenPaper}
      onRequireAuth={onRequireAuth}
    />
  );
}

describe("TrendingPapersPage", () => {
  beforeEach(() => {
    apiMocks.loadBrowserTrendingPapers.mockResolvedValue({
      date: "2026-04-21",
      sort: "default",
      page: 1,
      page_size: 20,
      has_next: true,
      has_previous: false,
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
      abstract: "Full abstract with the main result and evaluation setup.",
      tldr: "Semantic Scholar TLDR",
      published: "2026-04-21",
      updated: "2022-01-04",
      primary_category: "cs.AI",
      categories: "cs.AI cs.LG",
      doi: "10.1234/trending",
      comment: "Accepted at TestConf",
      journal_ref: "Journal of Tests 1(2)",
      summary_short: "Technical summary",
      html_url: "https://example.test/2401.12345",
      pdf_url: "https://example.test/2401.12345.pdf",
      image_url: "https://example.test/preview.webp",
      citation_count: 42,
      influential_citation_count: 7,
      semantic_scholar_id: "s2-paper-1",
      code_url: "https://github.com/example/trending-paper",
      code_url_source: "hf_papers",
      code_index_status: "indexed",
      code_is_python: true,
      code_python_reason: "Python package detected",
      venue: "International Conference on Test Learning",
      venue_year: 2026,
      venue_source: "s2_bulk",
      top_author_s2_id: "author-1",
      source_type: "html",
      index_status: "indexed",
      indexed_at: "2026-04-22T00:00:00Z",
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
    expect(screen.getByText(/Latest shows the newest papers across the library/)).toBeTruthy();
    expect(screen.getByText(/Hottest shows the most-upvoted papers overall/)).toBeTruthy();
    const card = document.querySelector(".trending-card");
    expect(card?.querySelector(".trending-card-copy h2")?.textContent).toContain("Trending Paper");
    expect(card?.querySelector(".trending-card-hero")).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Paper" })).toBeNull();
    const voteButton = screen.getByRole("button", { name: "Upvote 12" });
    expect(voteButton.textContent).toBe("12");
    expect(voteButton.querySelector(".trending-vote-chevron")).toBeTruthy();
    expect(screen.getByText("Ada Lovelace · 21st Apr 2026")).toBeTruthy();
    expect(apiMocks.loadBrowserTrendingPapers).toHaveBeenCalledWith("2026-04-21", "default", 1, 20);

    fireEvent.click(screen.getByRole("tab", { name: "Hottest" }));

    await waitFor(() => {
      expect(apiMocks.loadBrowserTrendingPapers).toHaveBeenLastCalledWith(
        "2026-04-21",
        "upvotes",
        1,
        20,
      );
    });

    fireEvent.click(screen.getByText("Trending Paper"));

    await waitFor(() => {
      expect(screen.getByText("Summary")).toBeTruthy();
    });
    expect(screen.getByText("Technical summary")).toBeTruthy();
    expect(screen.getByText("Root note")).toBeTruthy();
    expect(screen.getByText("Reply note")).toBeTruthy();
    expect(document.querySelector(".trending-detail-hero")).toBeTruthy();
    const detailImageWrap = document.querySelector(".trending-detail-image-wrap");
    expect(detailImageWrap).toBeTruthy();
    expect(detailImageWrap?.querySelector(".trending-detail-image")?.getAttribute("src")).toBe(
      "https://example.test/preview.webp",
    );
    expect(document.querySelector(".trending-detail-hero .trending-detail-copy")).toBeNull();
    expect(screen.getByText("TL;DR")).toBeTruthy();
    expect(screen.getByText("Semantic Scholar TLDR")).toBeTruthy();
    expect(screen.getByText("Paper details")).toBeTruthy();
    expect(screen.getByText("Authors")).toBeTruthy();
    expect(screen.getByText("Published").nextElementSibling?.textContent).toContain(
      "21st Apr 2026",
    );
    expect(screen.getByText("Updated").nextElementSibling?.textContent).toMatch(/years ago/);
    expect(screen.getByText("Categories")).toBeTruthy();
    expect(screen.getByText("cs.AI cs.LG")).toBeTruthy();
    expect(screen.getByText("Citations")).toBeTruthy();
    expect(screen.getByText("42")).toBeTruthy();
    expect(screen.getByText("DOI").nextElementSibling?.textContent).toContain("10.1234/trending");
    expect(screen.getByText("Code URL").nextElementSibling?.textContent).toContain(
      "https://github.com/example/trending-paper",
    );
    expect(screen.queryByText("Paperbase ID")).toBeNull();
    expect(screen.queryByText("HTML URL")).toBeNull();
    expect(screen.queryByText("arXiv comment")).toBeNull();
    expect(screen.queryByText("Influential citations")).toBeNull();
    expect(screen.queryByText("Code URL source")).toBeNull();
    expect(screen.queryByText("Python code")).toBeNull();
    expect(screen.queryByText("Code note")).toBeNull();
    expect(screen.queryByText("Paperbase upvotes")).toBeNull();
    expect(screen.queryByText("Semantic Scholar")).toBeNull();
    expect(screen.queryByText("Code index status")).toBeNull();
    expect(screen.queryByText("Source type")).toBeNull();
    expect(screen.queryByText("Index status")).toBeNull();
    expect(screen.queryByText("Indexed at")).toBeNull();
    expect(screen.getByText("Abstract")).toBeTruthy();
    expect(
      screen.getByText("Full abstract with the main result and evaluation setup."),
    ).toBeTruthy();
    expect(screen.getByRole("link", { name: "Read paper" }).getAttribute("href")).toBe(
      "https://example.test/2401.12345",
    );
    expect(screen.getByRole("link", { name: "PDF" }).getAttribute("href")).toBe(
      "https://example.test/2401.12345.pdf",
    );
  });

  it("hides vote controls on the landing feed", async () => {
    render(<Harness authenticated={false} variant="landing" />);

    await waitFor(() => {
      expect(screen.getByText("Trending Paper")).toBeTruthy();
    });

    expect(screen.queryByRole("button", { name: "Upvote 12" })).toBeNull();
    expect(document.querySelector(".trending-card-actions")).toBeNull();
  });

  it("delegates landing paper clicks instead of opening detail inline", async () => {
    const onOpenPaper = vi.fn();
    render(<Harness authenticated={false} variant="landing" onOpenPaper={onOpenPaper} />);

    await waitFor(() => {
      expect(screen.getByText("Trending Paper")).toBeTruthy();
    });

    fireEvent.click(screen.getByText("Trending Paper"));

    expect(onOpenPaper).toHaveBeenCalledWith({
      date: "2026-04-21",
      sort: "default",
      page: 1,
      paperId: "arxiv:2401.12345",
    });
    expect(apiMocks.getBrowserTrendingPaper).not.toHaveBeenCalled();
    expect(screen.queryByText("Summary")).toBeNull();
  });

  it("shows the empty state when the selected day has no papers", async () => {
    apiMocks.loadBrowserTrendingPapers.mockResolvedValueOnce({
      date: "2026-04-21",
      sort: "default",
      page: 1,
      page_size: 20,
      has_next: false,
      has_previous: false,
      papers: [],
    });

    render(<Harness />);

    await waitFor(() => {
      expect(screen.getByText(/No papers are ready here yet/)).toBeTruthy();
    });
  });

  it("loads the next page of 20 papers from the feed controls", async () => {
    render(<Harness />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Next 20" })).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("button", { name: "Next 20" }));

    await waitFor(() => {
      expect(apiMocks.loadBrowserTrendingPapers).toHaveBeenLastCalledWith(
        "2026-04-21",
        "default",
        2,
        20,
      );
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
