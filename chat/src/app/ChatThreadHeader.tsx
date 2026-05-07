import { useEffect, useRef } from "react";
import lottie from "lottie-web/build/player/lottie_light";
import type { AnimationItem } from "lottie-web";

import type { ThreadPane } from "./appTypes";
import { staticAssetPath } from "./appUtils";

type ChatThreadHeaderProps = {
  threadPane: ThreadPane;
  chatRunning: boolean;
  menuOpen: boolean;
  onSelectPane: (pane: ThreadPane) => void;
  onToggleMenu: () => void;
  onExportMarkdown: () => void;
  onExportPdf: () => void;
  onRename: () => void;
  onDelete: () => void;
};

export function ChatThreadHeader({
  threadPane,
  chatRunning,
  menuOpen,
  onSelectPane,
  onToggleMenu,
  onExportMarkdown,
  onExportPdf,
  onRename,
  onDelete,
}: ChatThreadHeaderProps) {
  const conversationIconRef = useRef<HTMLSpanElement | null>(null);
  const conversationAnimationRef = useRef<AnimationItem | null>(null);

  useEffect(() => {
    if (!conversationIconRef.current) {
      return;
    }

    const animation = lottie.loadAnimation({
      container: conversationIconRef.current,
      renderer: "svg",
      loop: true,
      autoplay: false,
      path: staticAssetPath("icons8-conversation.json"),
      rendererSettings: {
        preserveAspectRatio: "xMidYMid meet",
      },
    });
    conversationAnimationRef.current = animation;
    animation.goToAndStop(0, true);

    return () => {
      animation.destroy();
      if (conversationAnimationRef.current === animation) {
        conversationAnimationRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    const animation = conversationAnimationRef.current;
    if (!animation) {
      return;
    }
    if (chatRunning) {
      animation.play();
      return;
    }
    animation.goToAndStop(0, true);
  }, [chatRunning]);

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
            <span
              ref={conversationIconRef}
              className={`chat-thread-tab-lottie${chatRunning ? " running" : ""}`}
              aria-hidden="true"
            />
            <span>Conversation</span>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={threadPane === "papers"}
            className={`chat-thread-tab${threadPane === "papers" ? " active" : ""}`}
            onClick={() => onSelectPane("papers")}
          >
            <img
              src={staticAssetPath("icons8-research.svg")}
              className="chat-thread-tab-icon"
              alt=""
              aria-hidden="true"
            />
            <span>Research papers</span>
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
            <img
              src={staticAssetPath("menu.svg")}
              className="chat-thread-menu-icon"
              alt=""
              aria-hidden="true"
            />
          </button>
          {menuOpen ? (
            <div className="chat-thread-menu" role="menu">
              <button type="button" role="menuitem" onClick={onExportMarkdown}>
                Export summary as markdown
              </button>
              <button type="button" role="menuitem" onClick={onExportPdf}>
                Export summary as PDF
              </button>
              <div className="chat-thread-menu-divider" role="separator" />
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
