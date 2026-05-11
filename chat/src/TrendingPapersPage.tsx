import { CSSProperties, FormEvent, useEffect, useState } from "react";

import {
  createBrowserTrendingPaperComment,
  getBrowserTrendingPaper,
  loadBrowserTrendingPapers,
  removeUpvoteBrowserTrendingPaper,
  upvoteBrowserTrendingPaper,
} from "./api";
import type {
  PaperComment,
  PaperCommentsResponse,
  PaperVoteSummary,
  TrendingFeedResponse,
  TrendingPaperDetail,
} from "./types";

export type TrendingSort = "default" | "upvotes";

export type TrendingBrowseState = {
  date: string;
  sort: TrendingSort;
  page: number;
  paperId: string | null;
};

const DEFAULT_TRENDING_PAGE_SIZE = 20;

type TrendingPapersPageProps = {
  variant: "landing" | "public" | "workspace";
  authenticated: boolean;
  browseState: TrendingBrowseState;
  onChangeBrowseState: (nextState: TrendingBrowseState) => void;
  onOpenPaper?: (nextState: TrendingBrowseState) => void;
  onReadPaper?: (detail: TrendingPaperDetail) => void;
  onRequireAuth: () => void;
  feedDescription?: string | null;
  feedTitle?: string;
  pageSize?: number;
  showPagination?: boolean;
};

function sortLabel(sort: TrendingSort) {
  return sort === "upvotes" ? "Hottest" : "Latest";
}

type TrendingMetadataRow = {
  label: string;
  value?: string;
  href?: string;
  links?: Array<{ label: string; href: string }>;
};

const ARXIV_CATEGORY_LABELS: Record<string, string> = {
  "cs.AI": "Artificial Intelligence",
  "cs.AR": "Hardware Architecture",
  "cs.CC": "Computational Complexity",
  "cs.CE": "Computational Engineering, Finance, and Science",
  "cs.CG": "Computational Geometry",
  "cs.CL": "Computation and Language",
  "cs.CR": "Cryptography and Security",
  "cs.CV": "Computer Vision and Pattern Recognition",
  "cs.CY": "Computers and Society",
  "cs.DB": "Databases",
  "cs.DC": "Distributed, Parallel, and Cluster Computing",
  "cs.DL": "Digital Libraries",
  "cs.DM": "Discrete Mathematics",
  "cs.DS": "Data Structures and Algorithms",
  "cs.ET": "Emerging Technologies",
  "cs.FL": "Formal Languages and Automata Theory",
  "cs.GL": "General Literature",
  "cs.GR": "Graphics",
  "cs.GT": "Computer Science and Game Theory",
  "cs.HC": "Human-Computer Interaction",
  "cs.IR": "Information Retrieval",
  "cs.IT": "Information Theory",
  "cs.LG": "Machine Learning",
  "cs.LO": "Logic in Computer Science",
  "cs.MA": "Multiagent Systems",
  "cs.MM": "Multimedia",
  "cs.MS": "Mathematical Software",
  "cs.NA": "Numerical Analysis",
  "cs.NE": "Neural and Evolutionary Computing",
  "cs.NI": "Networking and Internet Architecture",
  "cs.OH": "Other Computer Science",
  "cs.OS": "Operating Systems",
  "cs.PF": "Performance",
  "cs.PL": "Programming Languages",
  "cs.RO": "Robotics",
  "cs.SC": "Symbolic Computation",
  "cs.SD": "Sound",
  "cs.SE": "Software Engineering",
  "cs.SI": "Social and Information Networks",
  "cs.SY": "Systems and Control",
  "econ.EM": "Econometrics",
  "econ.GN": "General Economics",
  "econ.TH": "Theoretical Economics",
  "math.IT": "Information Theory",
  "math.OC": "Optimization and Control",
  "q-bio.NC": "Neurons and Cognition",
  "stat.AP": "Applications",
  "stat.CO": "Computation",
  "stat.ME": "Methodology",
  "stat.ML": "Machine Learning",
  "stat.TH": "Statistics Theory",
};

function cleanMetadataValue(value: string | number | boolean | null | undefined) {
  if (value === null || value === undefined) {
    return "";
  }
  if (typeof value === "boolean") {
    return value ? "Yes" : "No";
  }
  return String(value).trim();
}

function formatMetadataCount(value: number | null | undefined) {
  return typeof value === "number" ? new Intl.NumberFormat().format(value) : "";
}

function formatArxivCategory(value: string | null | undefined) {
  const code = (value ?? "").trim();
  if (!code) {
    return "";
  }
  const label = ARXIV_CATEGORY_LABELS[code];
  return label ? `${label} (${code})` : code;
}

function formatArxivCategories(value: string | null | undefined) {
  const seen = new Set<string>();
  return (value ?? "")
    .split(/[\s,]+/)
    .map((category) => category.trim())
    .filter((category) => {
      if (!category || seen.has(category)) {
        return false;
      }
      seen.add(category);
      return true;
    })
    .map(formatArxivCategory)
    .join(", ");
}

function parseMetadataDate(value: string | null | undefined) {
  if (!value) {
    return null;
  }
  const dateOnly = value.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  const date = dateOnly
    ? new Date(Number(dateOnly[1]), Number(dateOnly[2]) - 1, Number(dateOnly[3]))
    : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function ordinalDay(day: number) {
  const remainder = day % 100;
  if (remainder >= 11 && remainder <= 13) {
    return `${day}th`;
  }
  switch (day % 10) {
    case 1:
      return `${day}st`;
    case 2:
      return `${day}nd`;
    case 3:
      return `${day}rd`;
    default:
      return `${day}th`;
  }
}

function formatPublishedDate(value: string | null | undefined) {
  const date = parseMetadataDate(value);
  if (!date) {
    return value ?? "";
  }
  const month = date.toLocaleString("en-US", { month: "short" });
  return `${ordinalDay(date.getDate())} ${month} ${date.getFullYear()}`;
}

function formatRelativeMetadataDate(value: string | null | undefined) {
  const date = parseMetadataDate(value);
  if (!date) {
    return value ?? "";
  }
  const now = new Date();
  const diffMs = date.getTime() - now.getTime();
  const absDays = Math.floor(Math.abs(diffMs) / 86_400_000);
  const suffix = diffMs > 0 ? "from now" : "ago";
  if (absDays === 0) {
    return "Today";
  }
  if (absDays === 1) {
    return diffMs > 0 ? "Tomorrow" : "Yesterday";
  }
  const units = [
    { name: "year", days: 365 },
    { name: "month", days: 30 },
    { name: "week", days: 7 },
    { name: "day", days: 1 },
  ];
  const unit = units.find((candidate) => absDays >= candidate.days) ?? units[units.length - 1];
  const amount = Math.max(1, Math.floor(absDays / unit.days));
  return `${amount} ${unit.name}${amount === 1 ? "" : "s"} ${suffix}`;
}

function normalizeDetailText(value: string | null | undefined) {
  return (value ?? "").replace(/\s+/g, " ").trim().toLowerCase();
}

function doiUrl(doi: string) {
  const trimmed = doi.trim();
  return trimmed.startsWith("http://") || trimmed.startsWith("https://")
    ? trimmed
    : `https://doi.org/${trimmed}`;
}

function buildTrendingMetadataRows(detail: TrendingPaperDetail): TrendingMetadataRow[] {
  const rows: TrendingMetadataRow[] = [];
  const add = (
    label: string,
    value: string | number | boolean | null | undefined,
    href?: string | null,
  ) => {
    const cleanValue = cleanMetadataValue(value);
    if (!cleanValue) {
      return;
    }
    rows.push({ label, value: cleanValue, href: href ?? undefined });
  };
  const addLinks = (label: string, links: Array<{ label: string; href: string | null }>) => {
    const cleanLinks = links
      .map((link) => ({
        label: cleanMetadataValue(link.label),
        href: cleanMetadataValue(link.href),
      }))
      .filter((link) => link.label && link.href);
    if (!cleanLinks.length) {
      return;
    }
    rows.push({ label, links: cleanLinks });
  };
  const venue = [detail.venue, detail.venue_year].filter(Boolean).join(" ");
  const primaryCategory = formatArxivCategory(detail.primary_category);
  const categories = formatArxivCategories(detail.categories);

  add("Authors", detail.authors);
  addLinks("arXiv", [
    {
      label: detail.arxiv_id,
      href: detail.arxiv_id ? `https://arxiv.org/abs/${detail.arxiv_id}` : null,
    },
    { label: "PDF", href: detail.pdf_url },
  ]);
  add("Primary category", primaryCategory);
  if (detail.categories !== detail.primary_category) {
    add("Categories", categories);
  }
  add("Published", formatPublishedDate(detail.published));
  add("Updated", formatRelativeMetadataDate(detail.updated));
  add("Venue", venue);
  add("Venue source", detail.venue_source);
  add("Journal reference", detail.journal_ref);
  add("DOI", detail.doi, detail.doi ? doiUrl(detail.doi) : null);
  add("Citations", formatMetadataCount(detail.citation_count));
  add(
    "Top author S2 ID",
    detail.top_author_s2_id,
    detail.top_author_s2_id
      ? `https://www.semanticscholar.org/author/${detail.top_author_s2_id}`
      : null,
  );
  add("Code URL", detail.code_url, detail.code_url);

  return rows;
}

function TrendingVoteButton({
  upvoteCount,
  viewerHasUpvoted,
  pending,
  onClick,
}: {
  upvoteCount: number;
  viewerHasUpvoted: boolean;
  pending: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={`trending-vote-button${viewerHasUpvoted ? " active" : ""}`}
      aria-label={viewerHasUpvoted ? `Remove upvote ${upvoteCount}` : `Upvote ${upvoteCount}`}
      aria-pressed={viewerHasUpvoted}
      onClick={onClick}
      disabled={pending}
    >
      <span className="trending-vote-chevron" aria-hidden="true" />
      <span className="trending-vote-count">{pending ? "..." : upvoteCount}</span>
    </button>
  );
}

function applyVoteToFeed(
  feed: TrendingFeedResponse | null,
  paperId: string,
  summary: PaperVoteSummary,
) {
  if (!feed) {
    return feed;
  }
  return {
    ...feed,
    papers: feed.papers.map((paper) =>
      paper.paper_id === paperId
        ? {
            ...paper,
            upvote_count: summary.upvote_count,
            viewer_has_upvoted: summary.viewer_has_upvoted,
          }
        : paper,
    ),
  };
}

function applyVoteToDetail(
  detail: TrendingPaperDetail | null,
  paperId: string,
  summary: PaperVoteSummary,
): TrendingPaperDetail | null {
  if (!detail || detail.paper_id !== paperId) {
    return detail;
  }
  return {
    ...detail,
    upvote_count: summary.upvote_count,
    viewer_has_upvoted: summary.viewer_has_upvoted,
  };
}

function CommentThread({
  comment,
  depth,
  authenticated,
  replyingTo,
  replyDrafts,
  commentSubmittingTarget,
  onReplyToggle,
  onReplyDraftChange,
  onReplySubmit,
  onRequireAuth,
}: {
  comment: PaperComment;
  depth: number;
  authenticated: boolean;
  replyingTo: string | null;
  replyDrafts: Record<string, string>;
  commentSubmittingTarget: string | "root" | null;
  onReplyToggle: (commentId: string) => void;
  onReplyDraftChange: (commentId: string, value: string) => void;
  onReplySubmit: (commentId: string) => void;
  onRequireAuth: () => void;
}) {
  const visualDepth = Math.min(depth, 3);
  const replyDraft = replyDrafts[comment.comment_id] ?? "";
  const replySubmitting = commentSubmittingTarget === comment.comment_id;
  return (
    <article
      className="trending-comment"
      style={{ "--comment-depth": String(visualDepth) } as CSSProperties}
    >
      <header className="trending-comment-header">
        <strong>{comment.author.display_name}</strong>
      </header>
      <p className="trending-comment-body">{comment.body}</p>
      <div className="trending-comment-actions">
        <button
          type="button"
          className="trending-link-button"
          onClick={() => {
            if (!authenticated) {
              onRequireAuth();
              return;
            }
            onReplyToggle(comment.comment_id);
          }}
        >
          Reply
        </button>
      </div>
      {replyingTo === comment.comment_id ? (
        <form
          className="trending-comment-form trending-comment-form-reply"
          onSubmit={(event) => {
            event.preventDefault();
            onReplySubmit(comment.comment_id);
          }}
        >
          <textarea
            value={replyDraft}
            onChange={(event) => onReplyDraftChange(comment.comment_id, event.target.value)}
            placeholder="Write a reply"
            rows={3}
          />
          <div className="trending-comment-form-actions">
            <button type="submit" className="primary-button" disabled={replySubmitting}>
              {replySubmitting ? "Posting..." : "Post reply"}
            </button>
          </div>
        </form>
      ) : null}
      {comment.replies.length > 0 ? (
        <div className="trending-comment-replies">
          {comment.replies.map((reply) => (
            <CommentThread
              key={reply.comment_id}
              comment={reply}
              depth={depth + 1}
              authenticated={authenticated}
              replyingTo={replyingTo}
              replyDrafts={replyDrafts}
              commentSubmittingTarget={commentSubmittingTarget}
              onReplyToggle={onReplyToggle}
              onReplyDraftChange={onReplyDraftChange}
              onReplySubmit={onReplySubmit}
              onRequireAuth={onRequireAuth}
            />
          ))}
        </div>
      ) : null}
    </article>
  );
}

export function TrendingPapersPage({
  variant,
  authenticated,
  browseState,
  onChangeBrowseState,
  onOpenPaper,
  onReadPaper,
  onRequireAuth,
  feedDescription,
  feedTitle = "Trending papers",
  pageSize,
  showPagination = true,
}: TrendingPapersPageProps) {
  const [feed, setFeed] = useState<TrendingFeedResponse | null>(null);
  const [feedStatus, setFeedStatus] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [feedError, setFeedError] = useState("");
  const [detail, setDetail] = useState<TrendingPaperDetail | null>(null);
  const [detailStatus, setDetailStatus] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [detailError, setDetailError] = useState("");
  const [votePendingPaperId, setVotePendingPaperId] = useState<string | null>(null);
  const [commentDraft, setCommentDraft] = useState("");
  const [replyDrafts, setReplyDrafts] = useState<Record<string, string>>({});
  const [activeReplyId, setActiveReplyId] = useState<string | null>(null);
  const [commentSubmittingTarget, setCommentSubmittingTarget] = useState<string | "root" | null>(
    null,
  );
  const effectivePageSize = pageSize ?? DEFAULT_TRENDING_PAGE_SIZE;

  useEffect(() => {
    let cancelled = false;
    setFeedStatus("loading");
    setFeedError("");
    void (async () => {
      try {
        const nextFeed = await loadBrowserTrendingPapers(
          browseState.date,
          browseState.sort,
          browseState.page,
          effectivePageSize,
        );
        if (cancelled) {
          return;
        }
        setFeed(nextFeed);
        setFeedStatus("ready");
      } catch (error) {
        if (cancelled) {
          return;
        }
        setFeedStatus("error");
        setFeedError(
          error instanceof Error ? error.message : "Trending papers could not be loaded.",
        );
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [browseState.date, browseState.page, browseState.sort, effectivePageSize]);

  useEffect(() => {
    if (!browseState.paperId) {
      setDetail(null);
      setDetailStatus("idle");
      setDetailError("");
      return;
    }
    let cancelled = false;
    setDetailStatus("loading");
    setDetailError("");
    void (async () => {
      try {
        const nextDetail = await getBrowserTrendingPaper(browseState.paperId!);
        if (cancelled) {
          return;
        }
        setDetail(nextDetail);
        setDetailStatus("ready");
      } catch (error) {
        if (cancelled) {
          return;
        }
        setDetailStatus("error");
        setDetailError(
          error instanceof Error ? error.message : "Paper detail could not be loaded.",
        );
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [browseState.paperId]);

  const detailDescription =
    detail && browseState.paperId
      ? [
          detail.primary_category || detail.categories,
          detail.published && `Published ${formatPublishedDate(detail.published)}`,
        ]
          .filter(Boolean)
          .join(" · ")
      : `Published ${formatPublishedDate(browseState.date)}`;
  const pageDescription = browseState.paperId
    ? detailDescription
    : (feedDescription ??
      "Explore research papers that are ready to read in Paperbase. Latest shows the newest papers across the library, while Hottest shows the most-upvoted papers overall.");

  const handleSortChange = (sort: TrendingSort) => {
    onChangeBrowseState({ ...browseState, sort, page: 1, paperId: null });
  };

  const handleOpenDetail = (paperId: string) => {
    const nextState = { ...browseState, paperId };
    if (onOpenPaper) {
      onOpenPaper(nextState);
      return;
    }
    onChangeBrowseState(nextState);
  };

  const handleBackToFeed = () => {
    onChangeBrowseState({ ...browseState, paperId: null });
  };

  const handlePageChange = (page: number) => {
    onChangeBrowseState({ ...browseState, page: Math.max(1, page), paperId: null });
  };

  const handleVote = async (paperId: string, hasUpvoted: boolean) => {
    if (!authenticated) {
      onRequireAuth();
      return;
    }
    setVotePendingPaperId(paperId);
    try {
      const summary = hasUpvoted
        ? await removeUpvoteBrowserTrendingPaper(paperId)
        : await upvoteBrowserTrendingPaper(paperId);
      setFeed((current) => applyVoteToFeed(current, paperId, summary));
      setDetail((current) => applyVoteToDetail(current, paperId, summary));
    } catch (error) {
      console.error(error);
    } finally {
      setVotePendingPaperId(null);
    }
  };

  const applyComments = (response: PaperCommentsResponse) => {
    setDetail((current) =>
      current && current.paper_id === response.paper_id
        ? { ...current, comments: response.comments }
        : current,
    );
  };

  const submitComment = async (parentCommentId: string | null) => {
    if (!detail) {
      return;
    }
    if (!authenticated) {
      onRequireAuth();
      return;
    }
    const draft = parentCommentId ? (replyDrafts[parentCommentId] ?? "") : commentDraft;
    if (!draft.trim()) {
      return;
    }
    const target = parentCommentId ?? "root";
    setCommentSubmittingTarget(target);
    try {
      const response = await createBrowserTrendingPaperComment(detail.paper_id, {
        body: draft,
        parent_comment_id: parentCommentId,
      });
      applyComments(response);
      if (parentCommentId) {
        setReplyDrafts((current) => ({ ...current, [parentCommentId]: "" }));
        setActiveReplyId(null);
      } else {
        setCommentDraft("");
      }
    } catch (error) {
      console.error(error);
    } finally {
      setCommentSubmittingTarget(null);
    }
  };

  const emptyState = feedStatus === "ready" && (feed?.papers.length ?? 0) === 0;
  const currentPage = feed?.page ?? browseState.page;
  const currentPageSize = feed?.page_size ?? effectivePageSize;
  const hasPreviousPage = feed?.has_previous ?? browseState.page > 1;
  const hasNextPage = feed?.has_next ?? false;
  const showPager = showPagination && (hasPreviousPage || hasNextPage);
  const headingText = browseState.paperId ? (detail?.title ?? "Loading paper") : feedTitle;
  const showVotes = variant !== "landing";
  const metadataRows = detail ? buildTrendingMetadataRows(detail) : [];
  const summaryText = detail?.summary_short?.trim() || "Summary is still being prepared.";
  const showTldr =
    Boolean(detail?.tldr?.trim()) &&
    normalizeDetailText(detail?.tldr) !== normalizeDetailText(summaryText);
  const showAbstract =
    Boolean(detail?.abstract?.trim()) &&
    normalizeDetailText(detail?.abstract) !== normalizeDetailText(summaryText) &&
    normalizeDetailText(detail?.abstract) !== normalizeDetailText(detail?.tldr);

  return (
    <section
      className={`trending-page trending-page-${variant}${browseState.paperId ? " detail-open" : ""}`}
      data-testid="trending-papers-page"
    >
      <header className="workspace-page-header trending-page-header">
        <div className="trending-page-header-copy">
          {variant === "landing" ? <h2>{headingText}</h2> : <h1>{headingText}</h1>}
          {pageDescription.trim() ? <p>{pageDescription}</p> : null}
        </div>
        <div className="trending-page-header-actions">
          {browseState.paperId ? (
            <button
              type="button"
              className="trending-link-button trending-back-button"
              onClick={handleBackToFeed}
            >
              Back to feed
            </button>
          ) : (
            <div className="trending-sort-toggle" role="tablist" aria-label="Trending sort">
              {(["default", "upvotes"] as TrendingSort[]).map((sort) => (
                <button
                  key={sort}
                  type="button"
                  role="tab"
                  aria-selected={browseState.sort === sort}
                  className={`trending-sort-button${browseState.sort === sort ? " active" : ""}`}
                  onClick={() => handleSortChange(sort)}
                >
                  {sortLabel(sort)}
                </button>
              ))}
            </div>
          )}
        </div>
      </header>

      {!browseState.paperId ? (
        <div className="trending-feed">
          {feedStatus === "loading" ? (
            <div className="trending-empty-state">Loading trending papers…</div>
          ) : null}
          {feedStatus === "error" ? (
            <div className="trending-empty-state warning">{feedError}</div>
          ) : null}
          {emptyState ? (
            <div className="trending-empty-state">
              {browseState.page > 1
                ? "No more papers are ready on this page."
                : "No papers are ready here yet. Check back soon as the library grows."}
            </div>
          ) : null}
          {feed?.papers.length ? (
            <>
              <div className="trending-card-grid">
                {feed.papers.map((paper) => {
                  const votePending = votePendingPaperId === paper.paper_id;
                  return (
                    <article key={paper.paper_id} className="trending-card">
                      <div className="trending-card-copy">
                        <h2>
                          <button
                            type="button"
                            className="trending-card-title-button"
                            onClick={() => handleOpenDetail(paper.paper_id)}
                          >
                            {paper.title}
                          </button>
                        </h2>
                        <span>
                          {paper.authors}
                          {paper.published ? ` · ${formatPublishedDate(paper.published)}` : ""}
                        </span>
                      </div>
                      <button
                        type="button"
                        className="trending-card-hero"
                        aria-label={`Open ${paper.title} preview`}
                        onClick={() => handleOpenDetail(paper.paper_id)}
                      >
                        {paper.image_url ? (
                          <img
                            src={paper.image_url}
                            alt={paper.title}
                            className="trending-card-image"
                          />
                        ) : (
                          <div className="trending-card-image trending-card-image-fallback">
                            Preview unavailable
                          </div>
                        )}
                      </button>
                      {showVotes ? (
                        <div className="trending-card-actions">
                          <TrendingVoteButton
                            upvoteCount={paper.upvote_count}
                            viewerHasUpvoted={paper.viewer_has_upvoted}
                            pending={votePending}
                            onClick={() =>
                              void handleVote(paper.paper_id, paper.viewer_has_upvoted)
                            }
                          />
                        </div>
                      ) : null}
                    </article>
                  );
                })}
              </div>
              {showPager ? (
                <nav className="trending-pagination" aria-label="Trending pagination">
                  <button
                    type="button"
                    className="trending-card-link"
                    disabled={!hasPreviousPage || feedStatus === "loading"}
                    onClick={() => handlePageChange(currentPage - 1)}
                  >
                    Previous {currentPageSize}
                  </button>
                  <span>Page {currentPage}</span>
                  <button
                    type="button"
                    className="trending-card-link"
                    disabled={!hasNextPage || feedStatus === "loading"}
                    onClick={() => handlePageChange(currentPage + 1)}
                  >
                    Next {currentPageSize}
                  </button>
                </nav>
              ) : null}
            </>
          ) : null}
        </div>
      ) : (
        <div className="trending-detail">
          {detailStatus === "loading" ? (
            <div className="trending-empty-state">Loading paper…</div>
          ) : null}
          {detailStatus === "error" ? (
            <div className="trending-empty-state warning">{detailError}</div>
          ) : null}
          {detail && detailStatus === "ready" ? (
            <>
              <section className="trending-detail-hero">
                <div className="trending-detail-image-wrap">
                  {detail.image_url ? (
                    <img
                      src={detail.image_url}
                      alt={detail.title}
                      className="trending-detail-image"
                    />
                  ) : (
                    <div className="trending-detail-image trending-card-image-fallback">
                      Preview unavailable
                    </div>
                  )}
                </div>
              </section>

              <div className="trending-detail-copy">
                <div className="trending-detail-actions">
                  <button
                    type="button"
                    className="trending-card-link"
                    onClick={() => {
                      if (onReadPaper) {
                        onReadPaper(detail);
                      } else {
                        onRequireAuth();
                      }
                    }}
                  >
                    Read paper
                  </button>
                  <a
                    href={detail.pdf_url}
                    target="_blank"
                    rel="noreferrer"
                    className="trending-card-link"
                  >
                    PDF
                  </a>
                  {showVotes ? (
                    <TrendingVoteButton
                      upvoteCount={detail.upvote_count}
                      viewerHasUpvoted={detail.viewer_has_upvoted}
                      pending={votePendingPaperId === detail.paper_id}
                      onClick={() => void handleVote(detail.paper_id, detail.viewer_has_upvoted)}
                    />
                  ) : null}
                </div>

                <section className="trending-summary-section">
                  <h2>Summary</h2>
                  <p>{summaryText}</p>
                </section>

                {showTldr ? (
                  <section className="trending-summary-section">
                    <h2>TL;DR</h2>
                    <p>{detail.tldr}</p>
                  </section>
                ) : null}

                {metadataRows.length ? (
                  <section className="trending-metadata-section">
                    <h2>Paper details</h2>
                    <dl className="trending-metadata-grid">
                      {metadataRows.map((row) => (
                        <div key={row.label} className="trending-metadata-row">
                          <dt>{row.label}</dt>
                          <dd>
                            {row.links?.length ? (
                              <span className="trending-metadata-link-list">
                                {row.links.map((link, index) => (
                                  <span key={`${link.label}-${link.href}`}>
                                    {index > 0 ? <span aria-hidden="true"> · </span> : null}
                                    <a href={link.href} target="_blank" rel="noreferrer">
                                      {link.label}
                                    </a>
                                  </span>
                                ))}
                              </span>
                            ) : row.href ? (
                              <a href={row.href} target="_blank" rel="noreferrer">
                                {row.value}
                              </a>
                            ) : (
                              row.value
                            )}
                          </dd>
                        </div>
                      ))}
                    </dl>
                  </section>
                ) : null}

                {showAbstract ? (
                  <section className="trending-summary-section">
                    <h2>Abstract</h2>
                    <p>{detail.abstract}</p>
                  </section>
                ) : null}
              </div>

              <section className="trending-comments-section">
                <div className="trending-section-heading">
                  <h2>Discussion</h2>
                  <p>Comment on the paper and reply in threads.</p>
                </div>
                {authenticated ? (
                  <form
                    className="trending-comment-form"
                    onSubmit={(event: FormEvent<HTMLFormElement>) => {
                      event.preventDefault();
                      void submitComment(null);
                    }}
                  >
                    <textarea
                      value={commentDraft}
                      onChange={(event) => setCommentDraft(event.target.value)}
                      placeholder="Share a thought about this paper"
                      rows={4}
                    />
                    <div className="trending-comment-form-actions">
                      <button
                        type="submit"
                        className="primary-button"
                        disabled={commentSubmittingTarget === "root"}
                      >
                        {commentSubmittingTarget === "root" ? "Posting..." : "Post comment"}
                      </button>
                    </div>
                  </form>
                ) : (
                  <div className="trending-auth-callout">
                    <p>
                      {showVotes
                        ? "Log in to upvote, comment, and reply."
                        : "Log in to comment and reply."}
                    </p>
                    <button type="button" className="primary-button" onClick={onRequireAuth}>
                      Log in
                    </button>
                  </div>
                )}

                <div className="trending-comments-list">
                  {detail.comments.length === 0 ? (
                    <div className="trending-empty-state">No comments yet.</div>
                  ) : (
                    detail.comments.map((comment) => (
                      <CommentThread
                        key={comment.comment_id}
                        comment={comment}
                        depth={0}
                        authenticated={authenticated}
                        replyingTo={activeReplyId}
                        replyDrafts={replyDrafts}
                        commentSubmittingTarget={commentSubmittingTarget}
                        onReplyToggle={(commentId) =>
                          setActiveReplyId((current) => (current === commentId ? null : commentId))
                        }
                        onReplyDraftChange={(commentId, value) =>
                          setReplyDrafts((current) => ({ ...current, [commentId]: value }))
                        }
                        onReplySubmit={(commentId) => void submitComment(commentId)}
                        onRequireAuth={onRequireAuth}
                      />
                    ))
                  )}
                </div>
              </section>
            </>
          ) : null}
        </div>
      )}
    </section>
  );
}
