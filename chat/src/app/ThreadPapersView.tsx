import type { ChatCitation } from "../types";
import type { PaperActionTarget, PaperCitationPreviewState } from "./appTypes";
import { groupCitationsByPaper } from "./appUtils";

type ThreadPapersViewProps = {
  citations: ChatCitation[];
  paperCitationPreviews: Record<string, PaperCitationPreviewState>;
  openPaperPdfModal: (target: PaperActionTarget) => void;
  openPaperExploration: (explorationId: string) => void;
  startPaperExploration: (target: PaperActionTarget) => void;
  paperExplorationIdsByPaperId: Record<string, string>;
};

export function ThreadPapersView({
  citations,
  paperCitationPreviews,
  openPaperPdfModal,
  openPaperExploration,
  startPaperExploration,
  paperExplorationIdsByPaperId,
}: ThreadPapersViewProps) {
  const paperGroups = groupCitationsByPaper(citations);
  return (
    <div className="thread-papers-view">
      {paperGroups.length > 0 ? (
        <div className="thread-paper-list">
          {paperGroups.map((group) => {
            const citation = group.citation;
            const paperPreview = citation.paper_id
              ? (paperCitationPreviews[citation.paper_id] ?? null)
              : null;
            const paper = paperPreview?.paper ?? null;
            const actionTarget: PaperActionTarget | null = citation.paper_id
              ? {
                  paperId: citation.paper_id,
                  path: citation.path,
                  paper,
                  title: paper?.title ?? citation.title,
                  authors: paper?.authors ?? "",
                  previewImageUrl: paperPreview?.preview?.image_url ?? null,
                  pdfUrl: paper?.pdf_url ?? null,
                }
              : null;
            const hasExploration = Boolean(
              citation.paper_id && paperExplorationIdsByPaperId[citation.paper_id],
            );
            const explorationId = citation.paper_id
              ? paperExplorationIdsByPaperId[citation.paper_id]
              : "";
            const openPaperTarget = () => {
              if (!actionTarget) {
                return;
              }
              if (explorationId) {
                openPaperExploration(explorationId);
                return;
              }
              openPaperPdfModal(actionTarget);
            };
            return (
              <article
                key={group.key}
                className={`thread-paper-card${actionTarget ? " clickable" : ""}`}
                onClick={openPaperTarget}
              >
                {group.citationCount > 1 ? (
                  <span className="paper-citation-count-badge">{group.citationCount}</span>
                ) : null}
                {paperPreview?.preview?.image_url ? (
                  <img
                    className="thread-paper-cover-image"
                    src={paperPreview.preview.image_url}
                    alt={`${paper?.title ?? citation.title} preview`}
                  />
                ) : (
                  <div className="thread-paper-cover-placeholder">Paper</div>
                )}
                <div className="thread-paper-card-main">
                  <h2>{paper?.title ?? citation.title}</h2>
                  {paper ? <div className="thread-paper-meta">{paper.authors}</div> : null}
                </div>
                <div className="thread-paper-actions">
                  {actionTarget ? (
                    <button
                      type="button"
                      className="citation-card-action"
                      onClick={(event) => {
                        event.stopPropagation();
                        startPaperExploration(actionTarget);
                      }}
                    >
                      {hasExploration ? "Open exploration" : "Start exploration"}
                    </button>
                  ) : null}
                  {paperPreview?.paper?.html_url ? (
                    <a
                      className="citation-card-action"
                      href={paperPreview.paper.html_url}
                      target="_blank"
                      rel="noreferrer"
                      onClick={(event) => event.stopPropagation()}
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
                      onClick={(event) => event.stopPropagation()}
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
