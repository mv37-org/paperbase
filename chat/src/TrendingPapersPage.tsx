import { CSSProperties, FormEvent, useEffect, useMemo, useState } from "react";

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
  paperId: string | null;
};

type TrendingPapersPageProps = {
  variant: "public" | "workspace";
  authenticated: boolean;
  browseState: TrendingBrowseState;
  onChangeBrowseState: (nextState: TrendingBrowseState) => void;
  onRequireAuth: () => void;
};

function formatTrendingDate(value: string) {
  const parsed = new Date(`${value}T12:00:00`);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat(undefined, {
    month: "long",
    day: "numeric",
    year: "numeric",
  }).format(parsed);
}

function sortLabel(sort: TrendingSort) {
  return sort === "upvotes" ? "Hottest" : "Latest";
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
  onRequireAuth,
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

  useEffect(() => {
    let cancelled = false;
    setFeedStatus("loading");
    setFeedError("");
    void (async () => {
      try {
        const nextFeed = await loadBrowserTrendingPapers(browseState.date, browseState.sort);
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
  }, [browseState.date, browseState.sort]);

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

  const pageTitle = useMemo(() => formatTrendingDate(browseState.date), [browseState.date]);
  const pageDescription = browseState.paperId
    ? `Published ${detail?.published ?? browseState.date}. Read the paper, skim the ELI5, and join the discussion.`
    : `Fresh papers people are opening today. Start with the newest releases, or switch to Hottest to see what is catching on.`;

  const handleSortChange = (sort: TrendingSort) => {
    onChangeBrowseState({ ...browseState, sort });
  };

  const handleOpenDetail = (paperId: string) => {
    onChangeBrowseState({ ...browseState, paperId });
  };

  const handleBackToFeed = () => {
    onChangeBrowseState({ ...browseState, paperId: null });
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

  return (
    <section
      className={`trending-page trending-page-${variant}${browseState.paperId ? " detail-open" : ""}`}
      data-testid="trending-papers-page"
    >
      <header className="workspace-page-header trending-page-header">
        <div className="trending-page-header-copy">
          <h1>{browseState.paperId ? (detail?.title ?? "Loading paper") : "Trending papers"}</h1>
          <p>
            {browseState.paperId ? pageDescription : `${pageDescription} Published ${pageTitle}.`}
          </p>
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
              No indexed papers were published on {pageTitle} yet.
            </div>
          ) : null}
          {feed?.papers.length ? (
            <div className="trending-card-grid">
              {feed.papers.map((paper) => {
                const votePending = votePendingPaperId === paper.paper_id;
                return (
                  <article key={paper.paper_id} className="trending-card">
                    <button
                      type="button"
                      className="trending-card-hero"
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
                    <div className="trending-card-footer">
                      <button
                        type="button"
                        className="trending-card-copy"
                        onClick={() => handleOpenDetail(paper.paper_id)}
                      >
                        <strong>{paper.title}</strong>
                        <span>{paper.authors}</span>
                      </button>
                      <div className="trending-card-actions">
                        <a
                          href={paper.html_url}
                          target="_blank"
                          rel="noreferrer"
                          className="trending-card-link"
                        >
                          Paper
                        </a>
                        <button
                          type="button"
                          className={`trending-vote-button${paper.viewer_has_upvoted ? " active" : ""}`}
                          onClick={() => void handleVote(paper.paper_id, paper.viewer_has_upvoted)}
                          disabled={votePending}
                        >
                          {votePending ? "..." : `Upvote ${paper.upvote_count}`}
                        </button>
                      </div>
                    </div>
                  </article>
                );
              })}
            </div>
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
                <div className="trending-detail-copy">
                  <div className="trending-detail-meta">
                    <span>{detail.authors}</span>
                    <span>
                      {detail.primary_category || detail.categories}
                      {detail.published ? ` · ${detail.published}` : ""}
                    </span>
                  </div>
                  <div className="trending-detail-actions">
                    <a
                      href={detail.html_url}
                      target="_blank"
                      rel="noreferrer"
                      className="trending-card-link"
                    >
                      Read paper
                    </a>
                    <a
                      href={detail.pdf_url}
                      target="_blank"
                      rel="noreferrer"
                      className="trending-card-link"
                    >
                      PDF
                    </a>
                    <button
                      type="button"
                      className={`trending-vote-button${detail.viewer_has_upvoted ? " active" : ""}`}
                      onClick={() => void handleVote(detail.paper_id, detail.viewer_has_upvoted)}
                      disabled={votePendingPaperId === detail.paper_id}
                    >
                      {votePendingPaperId === detail.paper_id
                        ? "..."
                        : `Upvote ${detail.upvote_count}`}
                    </button>
                  </div>
                  <section className="trending-summary-section">
                    <h2>ELI5 summary</h2>
                    <p>{detail.summary_eli5 || "Summary is still being prepared."}</p>
                  </section>
                </div>
              </section>

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
                    <p>Log in to upvote, comment, and reply.</p>
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
