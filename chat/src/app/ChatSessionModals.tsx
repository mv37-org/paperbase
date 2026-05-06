import type { Dispatch, SetStateAction } from "react";

import type { ExportFormat, ExportModalState, FeedbackModalState } from "./appTypes";

type ChatSessionModalsProps = {
  exportModalState: ExportModalState;
  onCloseExportModal: () => void;
  exportActiveThread: (format: ExportFormat) => void;
  feedbackModalState: FeedbackModalState;
  closeFeedbackModal: () => void;
  feedbackReasons: string[];
  setFeedbackReasons: Dispatch<SetStateAction<string[]>>;
  feedbackNote: string;
  setFeedbackNote: Dispatch<SetStateAction<string>>;
  feedbackSubmitting: boolean;
  submitFeedbackModal: () => void;
};

export function ChatSessionModals({
  exportModalState,
  onCloseExportModal,
  exportActiveThread,
  feedbackModalState,
  closeFeedbackModal,
  feedbackReasons,
  setFeedbackReasons,
  feedbackNote,
  setFeedbackNote,
  feedbackSubmitting,
  submitFeedbackModal,
}: ChatSessionModalsProps) {
  return (
    <>
      <div
        id="chat-export-modal"
        className={`modal${exportModalState ? "" : " hidden"}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="chat-export-modal-title"
      >
        <div className="modal-backdrop" onClick={onCloseExportModal} />
        <div className="modal-panel chat-export-modal-panel">
          <button
            className="modal-close"
            type="button"
            aria-label="Close export options"
            onClick={onCloseExportModal}
          >
            ×
          </button>
          <div className="modal-heading modal-heading-copy">
            <h2 id="chat-export-modal-title" className="modal-title">
              Download current session
            </h2>
            <p className="modal-caption">Choose the format for this chat session.</p>
          </div>
          <div className="chat-export-options">
            {(["pdf", "markdown", "docx"] as ExportFormat[]).map((format) => (
              <button
                key={format}
                type="button"
                className="chat-export-option"
                onClick={() => {
                  exportActiveThread(format);
                  onCloseExportModal();
                }}
              >
                <span className="chat-export-option-icon" aria-hidden="true">
                  {format === "pdf" ? "PDF" : format === "markdown" ? "MD" : "DOCX"}
                </span>
                <span>{format === "markdown" ? "Markdown" : format.toUpperCase()}</span>
              </button>
            ))}
          </div>
        </div>
      </div>

      <div
        id="chat-feedback-modal"
        className={`modal${feedbackModalState ? "" : " hidden"}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="chat-feedback-modal-title"
      >
        <div className="modal-backdrop" onClick={closeFeedbackModal} />
        <div className="modal-panel chat-feedback-modal-panel">
          <button
            className="modal-close"
            type="button"
            aria-label="Close feedback"
            onClick={closeFeedbackModal}
          >
            ×
          </button>
          <div className="modal-heading modal-heading-copy">
            <h2 id="chat-feedback-modal-title" className="modal-title">
              {feedbackModalState?.sentiment === "positive"
                ? "What did you like about this response? (optional)"
                : "What didn't you like about this response? (optional)"}
            </h2>
            <p className="modal-caption">The debug bundle is attached automatically.</p>
          </div>
          <div className="feedback-reason-grid">
            {(feedbackModalState?.sentiment === "positive"
              ? [
                  "Up to date",
                  "Accurate",
                  "Helpful",
                  "Followed instructions",
                  "Good sources",
                  "Other...",
                ]
              : ["Out of date", "Inaccurate", "Wrong sources", "Too long", "Too short", "Other..."]
            ).map((reason) => (
              <button
                key={reason}
                type="button"
                className={`feedback-reason${feedbackReasons.includes(reason) ? " selected" : ""}`}
                onClick={() =>
                  setFeedbackReasons((current) =>
                    current.includes(reason)
                      ? current.filter((item) => item !== reason)
                      : [...current, reason],
                  )
                }
              >
                {reason}
              </button>
            ))}
          </div>
          <textarea
            className="feedback-note-input"
            value={feedbackNote}
            onChange={(event) => setFeedbackNote(event.target.value)}
            placeholder="Add details"
            aria-label="Feedback details"
          />
          <div className="feedback-modal-actions">
            <button
              type="button"
              className="primary-button"
              disabled={feedbackSubmitting}
              onClick={submitFeedbackModal}
            >
              {feedbackSubmitting ? "Sending..." : "Send feedback"}
            </button>
            <button type="button" className="secondary-button" onClick={closeFeedbackModal}>
              Cancel
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
