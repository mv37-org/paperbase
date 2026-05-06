import type { ChatCitation } from "../types";
import type { PaperCitationPreviewState } from "./appTypes";
import { summarizeCitationPreview } from "./appUtils";

type ThreadPapersViewProps = {
  citations: ChatCitation[];
  paperCitationPreviews: Record<string, PaperCitationPreviewState>;
  openPaperDrawer: (paperId: string, path: string) => void;
};

export function ThreadPapersView({
  citations,
  paperCitationPreviews,
  openPaperDrawer,
}: ThreadPapersViewProps) {
  return (
    <div className="thread-papers-view">
      {citations.length > 0 ? (
        <div className="thread-paper-list">
          {citations.map((citation) => {
            const paperPreview = citation.paper_id
              ? (paperCitationPreviews[citation.paper_id] ?? null)
              : null;
            const paperSummary = summarizeCitationPreview(paperPreview?.paper ?? null);
            return (
              <article
                key={`${citation.source_id}:${citation.paper_id ?? citation.path}`}
                className="thread-paper-card"
              >
                <div className="thread-paper-card-main">
                  <span className="thread-paper-source">{citation.source_id}</span>
                  <h2>{paperPreview?.paper?.title ?? citation.title}</h2>
                  {paperPreview?.paper ? (
                    <div className="thread-paper-meta">
                      {paperPreview.paper.authors}
                      {paperPreview.paper.published ? ` · ${paperPreview.paper.published}` : ""}
                    </div>
                  ) : null}
                  {paperSummary ? (
                    <p className="thread-paper-summary">{paperSummary}</p>
                  ) : (
                    <p className="thread-paper-summary">{citation.excerpt}</p>
                  )}
                </div>
                <div className="thread-paper-actions">
                  {citation.paper_id ? (
                    <button
                      type="button"
                      className="citation-card-action"
                      onClick={() => openPaperDrawer(citation.paper_id!, citation.path)}
                    >
                      Open paper
                    </button>
                  ) : null}
                  {paperPreview?.paper?.html_url ? (
                    <a
                      className="citation-card-action"
                      href={paperPreview.paper.html_url}
                      target="_blank"
                      rel="noreferrer"
                    >
                      HTML
                    </a>
                  ) : null}
                  {paperPreview?.paper?.pdf_url ? (
                    <a
                      className="citation-card-action"
                      href={paperPreview.paper.pdf_url}
                      target="_blank"
                      rel="noreferrer"
                    >
                      PDF
                    </a>
                  ) : null}
                </div>
              </article>
            );
          })}
        </div>
      ) : (
        <div className="thread-papers-empty">No cited papers in this chat yet.</div>
      )}
    </div>
  );
}
