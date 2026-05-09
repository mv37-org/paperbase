import { useMemo, useRef, useState } from "react";
import type { UIEvent } from "react";

import { apiResourceUrl } from "./api";
import { AppIcon } from "./app/AppChrome";
import { staticAssetPath } from "./app/appUtils";
import { ProjectPresentationCanvas } from "./ProjectPresentationCanvas";
import type { ProjectPresentationResponse, ProjectPresentationSlideResponse } from "./types";

type ProjectPresentationDeckProps = {
  presentation: ProjectPresentationResponse;
  title: string;
  publicMode?: boolean;
  sharing?: boolean;
  regenerating?: boolean;
  onRegenerate?: () => void;
  onToggleShare?: (isPublic: boolean) => void;
  onCopyShareLink?: (url: string) => void;
};

function legacySlide(presentation: ProjectPresentationResponse): ProjectPresentationSlideResponse {
  const markdownTitle =
    presentation.summary_markdown
      .split("\n")
      .find((line) => line.trim().startsWith("# "))
      ?.replace(/^#\s+/, "")
      .trim() || "Presentation";
  const description =
    presentation.summary_markdown
      .replace(/^#+\s+/gm, "")
      .replace(/\|/g, " ")
      .replace(/\s+/g, " ")
      .trim()
      .split(" ")
      .slice(0, 64)
      .join(" ") || "Research presentation";
  return {
    slide_id: "legacy-slide",
    title: markdownTitle,
    description,
    visual_type: "code",
    visual_code: presentation.visual_code,
    image_prompt: "",
    image_url: "",
  };
}

function slidesForPresentation(presentation: ProjectPresentationResponse) {
  return presentation.slides.length > 0 ? presentation.slides : [legacySlide(presentation)];
}

export function ProjectPresentationDeck({
  presentation,
  title,
  publicMode = false,
  sharing = false,
  regenerating = false,
  onRegenerate,
  onToggleShare,
  onCopyShareLink,
}: ProjectPresentationDeckProps) {
  const slides = useMemo(() => slidesForPresentation(presentation), [presentation]);
  const [activeSlideIndex, setActiveSlideIndex] = useState(0);
  const [shareModalOpen, setShareModalOpen] = useState(false);
  const trackRef = useRef<HTMLDivElement | null>(null);
  const activeSlide = slides[Math.min(activeSlideIndex, slides.length - 1)] ?? slides[0];
  const imageUrl = activeSlide?.image_url ? apiResourceUrl(activeSlide.image_url) : "";

  const handleScroll = (event: UIEvent<HTMLDivElement>) => {
    const target = event.currentTarget;
    const nextIndex = Math.round(target.scrollTop / Math.max(target.clientHeight, 1));
    setActiveSlideIndex(Math.max(0, Math.min(slides.length - 1, nextIndex)));
  };

  const scrollToSlide = (index: number) => {
    const track = trackRef.current;
    if (!track) {
      setActiveSlideIndex(index);
      return;
    }
    track.scrollTo({ top: track.clientHeight * index, behavior: "smooth" });
  };

  return (
    <div className="project-presentation-layout project-presentation-slide-layout">
      <section className="project-presentation-canvas project-presentation-slide-canvas">
        <div key={activeSlide?.slide_id ?? "slide"} className="project-presentation-visual-stage">
          <ProjectPresentationCanvas
            visualCode={activeSlide?.visual_code ?? ""}
            imageUrl={imageUrl}
            title={activeSlide?.title || title}
          />
        </div>
      </section>

      <section className="project-presentation-summary project-presentation-slide-summary">
        <div className="project-presentation-fixed-header">
          <ProjectPresentationAudioPlayer presentation={presentation} />
          {!publicMode ? (
            <div className="project-presentation-header-actions">
              <button
                type="button"
                className="project-presentation-share-button"
                onClick={() => setShareModalOpen(true)}
              >
                Share this presentation
              </button>
              {onRegenerate ? (
                <button
                  type="button"
                  className="project-presentation-inline-regenerate-button"
                  disabled={regenerating}
                  onClick={onRegenerate}
                  aria-label="Regenerate presentation"
                  title="Regenerate presentation"
                >
                  <AppIcon
                    src={staticAssetPath("icons8-restart-50.svg")}
                    className="project-presentation-regenerate-icon"
                  />
                </button>
              ) : null}
            </div>
          ) : null}
        </div>

        <div ref={trackRef} className="project-presentation-slide-track" onScroll={handleScroll}>
          {slides.map((slide, index) => (
            <article
              key={slide.slide_id || index}
              className={`project-presentation-slide-copy${index === activeSlideIndex ? " active" : ""}`}
            >
              <span>{String(index + 1).padStart(2, "0")}</span>
              <h1>{slide.title}</h1>
              <p>{slide.description}</p>
            </article>
          ))}
        </div>

        <div className="project-presentation-slide-dots" aria-label="Presentation slides">
          {slides.map((slide, index) => (
            <button
              key={slide.slide_id || index}
              type="button"
              className={index === activeSlideIndex ? "active" : ""}
              onClick={() => scrollToSlide(index)}
              aria-label={`Show slide ${index + 1}`}
            />
          ))}
        </div>
      </section>

      {!publicMode && shareModalOpen ? (
        <div className="modal">
          <button
            type="button"
            className="modal-backdrop"
            aria-label="Close share presentation"
            onClick={() => setShareModalOpen(false)}
          />
          <div
            className="modal-panel project-presentation-share-modal"
            role="dialog"
            aria-modal="true"
          >
            <button
              type="button"
              className="modal-close"
              aria-label="Close share presentation"
              onClick={() => setShareModalOpen(false)}
            >
              ×
            </button>
            <div className="modal-heading modal-heading-copy">
              <h2 className="modal-title">Share this presentation</h2>
              <p className="modal-subtitle">
                Create a view-only webpage that anyone with the link can open.
              </p>
            </div>
            <label className="project-presentation-share-toggle">
              <input
                type="checkbox"
                checked={presentation.is_public}
                disabled={sharing}
                onChange={(event) => onToggleShare?.(event.target.checked)}
              />
              <span>Enable public share link</span>
            </label>
            {presentation.share_url ? (
              <div className="project-presentation-share-link-row">
                <input
                  value={presentation.share_url}
                  readOnly
                  aria-label="Presentation share link"
                />
                <button type="button" onClick={() => onCopyShareLink?.(presentation.share_url)}>
                  Copy link
                </button>
              </div>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function ProjectPresentationAudioPlayer({
  presentation,
}: {
  presentation: ProjectPresentationResponse;
}) {
  if (!presentation.audio_url) {
    return (
      <div className="project-presentation-audio-player empty">
        <div className="project-presentation-audio-copy">
          <span>Audio walkthrough</span>
          <small>Audio is not available for this presentation.</small>
        </div>
      </div>
    );
  }
  return (
    <div className="project-presentation-audio-player">
      <div className="project-presentation-audio-copy">
        <span>Audio walkthrough</span>
        <small>Two-person explainer</small>
      </div>
      <audio
        controls
        preload="metadata"
        src={apiResourceUrl(presentation.audio_url)}
        aria-label="Presentation audio walkthrough"
      />
    </div>
  );
}
