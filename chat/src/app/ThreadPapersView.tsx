import type { ChatCitation } from "../types";
import type { PaperActionTarget, PaperCitationPreviewState } from "./appTypes";
import { groupCitationsByPaper } from "./appUtils";

type ThreadPapersViewProps = {
  citations: ChatCitation[];
  paperCitationPreviews: Record<string, PaperCitationPreviewState>;
  openPaperPdfModal: (target: PaperActionTarget) => void;
};

export function ThreadPapersView({
  citations,
  paperCitationPreviews,
  openPaperPdfModal,
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
            const openPaperTarget = () => {
              if (!actionTarget) {
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
                <div className="thread-paper-card-main">
                  <h2>{paper?.title ?? citation.title}</h2>
                  {paper ? <div className="thread-paper-meta">{paper.authors}</div> : null}
                </div>
                {paperPreview?.preview?.image_url ? (
                  <img
                    className="thread-paper-cover-image"
                    src={paperPreview.preview.image_url}
                    alt={`${paper?.title ?? citation.title} preview`}
                  />
                ) : (
                  <div className="thread-paper-cover-placeholder">Paper</div>
                )}
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
