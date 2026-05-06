import type { ThreadPane } from "./appTypes";

type ChatThreadHeaderProps = {
  threadPane: ThreadPane;
  menuOpen: boolean;
  onSelectPane: (pane: ThreadPane) => void;
  onToggleMenu: () => void;
  onOpenExportSummary: () => void;
  onExportMarkdown: () => void;
  onExportPdf: () => void;
  onRename: () => void;
  onDelete: () => void;
};

export function ChatThreadHeader({
  threadPane,
  menuOpen,
  onSelectPane,
  onToggleMenu,
  onOpenExportSummary,
  onExportMarkdown,
  onExportPdf,
  onRename,
  onDelete,
}: ChatThreadHeaderProps) {
  return (
    <header className="chat-thread-header">
      <div className="chat-thread-header-inner">
        <div className="chat-thread-tabs" role="tablist" aria-label="Chat view">
          <button
            type="button"
            role="tab"
            aria-selected={threadPane === "chat"}
            className={`chat-thread-tab${threadPane === "chat" ? " active" : ""}`}
            onClick={() => onSelectPane("chat")}
          >
            Chat
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={threadPane === "papers"}
            className={`chat-thread-tab${threadPane === "papers" ? " active" : ""}`}
            onClick={() => onSelectPane("papers")}
          >
            Papers
          </button>
        </div>
        <div className="chat-thread-menu-wrap">
          <button
            type="button"
            className="chat-thread-menu-button"
            aria-label="Open chat actions"
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            onClick={onToggleMenu}
          >
            <span aria-hidden="true">•••</span>
          </button>
          {menuOpen ? (
            <div className="chat-thread-menu" role="menu">
              <button type="button" role="menuitem" onClick={onOpenExportSummary}>
                Export summary
              </button>
              <button type="button" role="menuitem" onClick={onExportMarkdown}>
                Export summary as Markdown
              </button>
              <button type="button" role="menuitem" onClick={onExportPdf}>
                Export summary as PDF
              </button>
              <button type="button" role="menuitem" onClick={onRename}>
                Rename
              </button>
              <button type="button" role="menuitem" className="danger" onClick={onDelete}>
                Delete
              </button>
            </div>
          ) : null}
        </div>
      </div>
    </header>
  );
}
