import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import {
  getDocument,
  GlobalWorkerOptions,
  type PDFDocumentLoadingTask,
  type RenderTask,
} from "pdfjs-dist/legacy/build/pdf.mjs";
import pdfWorkerUrl from "pdfjs-dist/legacy/build/pdf.worker.mjs?url";

GlobalWorkerOptions.workerSrc = pdfWorkerUrl;

export type PdfDocumentSection = {
  id: string;
  title: string;
  pageNumber: number;
  pageYOffsetRatio: number;
};

export type PdfDocumentViewerHandle = {
  scrollToSection: (sectionId: string) => void;
};

type PdfDocumentViewerProps = {
  url: string;
  title: string;
  className?: string;
  onSectionsChange?: (sections: PdfDocumentSection[]) => void;
};

type PdfViewerStatus = "loading" | "ready" | "error";

function clampPageYOffsetRatio(value: number) {
  if (!Number.isFinite(value)) {
    return 0;
  }
  return Math.max(0, Math.min(1, value));
}

function normalizeSectionTitle(title: string) {
  return title
    .replace(/\s+/g, " ")
    .replace(/^(?:section\s+)?(?:\d+(?:\.\d+)*|[IVX]+)\.?\s+/i, "")
    .trim();
}

function sectionKey(title: string) {
  return normalizeSectionTitle(title).toLowerCase();
}

function finalizeSections(sections: PdfDocumentSection[]) {
  const seen = new Set<string>();
  const nextSections: PdfDocumentSection[] = [];
  for (const section of sections) {
    const title = normalizeSectionTitle(section.title);
    if (!title) {
      continue;
    }
    const key = sectionKey(title);
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    const pageNumber = Number.isFinite(section.pageNumber) ? Math.floor(section.pageNumber) : 0;
    if (pageNumber < 1 || !Number.isFinite(section.pageYOffsetRatio)) {
      continue;
    }
    nextSections.push({
      id: `${key.replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "section"}-${pageNumber}`,
      title,
      pageNumber,
      pageYOffsetRatio: clampPageYOffsetRatio(section.pageYOffsetRatio),
    });
    if (nextSections.length >= 16) {
      break;
    }
  }
  return nextSections;
}

async function sectionTargetForDestination(
  pdf: Awaited<PDFDocumentLoadingTask["promise"]>,
  dest: unknown,
) {
  try {
    const explicitDest =
      typeof dest === "string" ? await pdf.getDestination(dest) : Array.isArray(dest) ? dest : null;
    const pageRef = explicitDest?.[0];
    if (pageRef === null || pageRef === undefined) {
      return null;
    }
    const pageNumber =
      typeof pageRef === "number" ? pageRef + 1 : (await pdf.getPageIndex(pageRef)) + 1;
    const page = await pdf.getPage(pageNumber);
    const viewport = page.getViewport({ scale: 1 });
    page.cleanup();
    const destinationTop = typeof explicitDest?.[3] === "number" ? explicitDest[3] : null;
    return {
      pageNumber,
      pageYOffsetRatio:
        destinationTop === null
          ? 0
          : clampPageYOffsetRatio((viewport.height - destinationTop) / viewport.height),
    };
  } catch {
    return null;
  }
}

async function extractOutlineSections(pdf: Awaited<PDFDocumentLoadingTask["promise"]>) {
  if (typeof pdf.getOutline !== "function") {
    return [];
  }
  const outline = await pdf.getOutline().catch(() => null);
  if (!outline?.length) {
    return [];
  }
  const sections: PdfDocumentSection[] = [];
  const visit = async (items: typeof outline) => {
    for (const item of items) {
      const title = normalizeSectionTitle(item.title ?? "");
      const target = await sectionTargetForDestination(pdf, item.dest);
      if (title && target) {
        sections.push({
          id: title,
          title,
          pageNumber: target.pageNumber,
          pageYOffsetRatio: target.pageYOffsetRatio,
        });
      }
      if (item.items?.length) {
        await visit(item.items);
      }
    }
  };
  await visit(outline);
  return finalizeSections(sections);
}

function textItemsToLines(items: unknown[]) {
  const lines: Array<{ y: number; chunks: Array<{ x: number; text: string }> }> = [];
  for (const item of items) {
    const entry = item as { str?: unknown; transform?: unknown };
    const text = typeof entry.str === "string" ? entry.str.trim() : "";
    if (!text) {
      continue;
    }
    const transform = Array.isArray(entry.transform) ? entry.transform : [];
    const x = typeof transform[4] === "number" ? transform[4] : 0;
    const y = typeof transform[5] === "number" ? transform[5] : 0;
    const line = lines.find((candidate) => Math.abs(candidate.y - y) < 3);
    if (line) {
      line.chunks.push({ x, text });
    } else {
      lines.push({ y, chunks: [{ x, text }] });
    }
  }
  return lines
    .sort((a, b) => b.y - a.y)
    .map((line) => ({
      y: line.y,
      text: line.chunks
        .sort((a, b) => a.x - b.x)
        .map((chunk) => chunk.text)
        .join(" ")
        .replace(/\s+/g, " ")
        .trim(),
    }))
    .filter((line) => line.text);
}

function sectionFromLine(
  line: { text: string; y: number },
  pageNumber: number,
  pageHeight: number,
): PdfDocumentSection | null {
  const text = line.text.replace(/\s+/g, " ").trim();
  if (text.length < 3 || text.length > 110 || /https?:\/\//i.test(text)) {
    return null;
  }
  if (/^(?:figure|table|algorithm)\s+\d+/i.test(text)) {
    return null;
  }
  const knownHeading =
    /^(?:abstract|introduction|related works?|background|methods?|methodology|approach|model|experiments?(?:\s*(?:&|and)\s*results?)?|results?|evaluation|analysis|discussion|limitations?|conclusions?|references|acknowledg(?:e)?ments?|appendix)\b/i.test(
      text,
    );
  const numberedHeading =
    /^(?:\d+(?:\.\d+)*|[IVX]+)\.?\s+[A-Z][A-Za-z0-9()[\]&:;,/+\-\s]{2,}$/i.test(text);
  if (!knownHeading && !numberedHeading) {
    return null;
  }
  const title = normalizeSectionTitle(text);
  if (!title || title.split(/\s+/).length > 12) {
    return null;
  }
  return {
    id: `${title}-${pageNumber}`,
    title,
    pageNumber,
    pageYOffsetRatio: clampPageYOffsetRatio((pageHeight - line.y) / pageHeight),
  };
}

function attachSectionAnchors(
  sections: PdfDocumentSection[],
  pageElements: Map<number, HTMLDivElement>,
  pageHeights: Map<number, number>,
  anchors: Map<string, HTMLElement>,
) {
  anchors.clear();
  for (const section of sections) {
    const pageElement = pageElements.get(section.pageNumber);
    const pageHeight = pageHeights.get(section.pageNumber);
    if (!pageElement || !pageHeight) {
      continue;
    }
    const anchor = document.createElement("span");
    anchor.className = "pdf-document-section-anchor";
    anchor.setAttribute("aria-hidden", "true");
    anchor.style.top = `${Math.floor(clampPageYOffsetRatio(section.pageYOffsetRatio) * pageHeight)}px`;
    pageElement.appendChild(anchor);
    anchors.set(section.id, anchor);
  }
}

export const PdfDocumentViewer = forwardRef<PdfDocumentViewerHandle, PdfDocumentViewerProps>(
  function PdfDocumentViewer({ url, title, className = "", onSectionsChange }, ref) {
    const scrollRef = useRef<HTMLDivElement | null>(null);
    const pagesRef = useRef<HTMLDivElement | null>(null);
    const onSectionsChangeRef = useRef(onSectionsChange);
    const sectionAnchorsRef = useRef(new Map<string, HTMLElement>());
    const [viewerWidth, setViewerWidth] = useState(0);
    const [status, setStatus] = useState<PdfViewerStatus>("loading");
    const [error, setError] = useState("");

    useImperativeHandle(
      ref,
      () => ({
        scrollToSection: (sectionId: string) => {
          sectionAnchorsRef.current.get(sectionId)?.scrollIntoView({
            behavior: "smooth",
            block: "start",
          });
        },
      }),
      [],
    );

    useEffect(() => {
      onSectionsChangeRef.current = onSectionsChange;
    }, [onSectionsChange]);

    useEffect(() => {
      const element = scrollRef.current;
      if (!element) {
        return;
      }

      const updateWidth = () => {
        const nextWidth = Math.floor(element.getBoundingClientRect().width);
        setViewerWidth((currentWidth) => (currentWidth === nextWidth ? currentWidth : nextWidth));
      };

      updateWidth();
      if (typeof ResizeObserver === "undefined") {
        window.addEventListener("resize", updateWidth);
        return () => window.removeEventListener("resize", updateWidth);
      }

      const resizeObserver = new ResizeObserver(updateWidth);
      resizeObserver.observe(element);
      return () => resizeObserver.disconnect();
    }, []);

    useEffect(() => {
      const container = pagesRef.current;
      if (!container) {
        return;
      }

      container.replaceChildren();
      sectionAnchorsRef.current.clear();
      setError("");

      if (!url) {
        setStatus("error");
        setError("No PDF URL is available.");
        onSectionsChangeRef.current?.([]);
        return;
      }

      if (viewerWidth <= 0) {
        setStatus("loading");
        return;
      }

      let cancelled = false;
      const renderTasks: RenderTask[] = [];
      const loadingTask: PDFDocumentLoadingTask = getDocument({ url, withCredentials: false });
      setStatus("loading");

      void (async () => {
        try {
          const pdf = await loadingTask.promise;
          if (cancelled) {
            return;
          }
          setStatus("ready");
          const outlineSections = await extractOutlineSections(pdf);
          const textSections: PdfDocumentSection[] = [];
          const pageElements = new Map<number, HTMLDivElement>();
          const pageHeights = new Map<number, number>();

          for (let pageNumber = 1; pageNumber <= pdf.numPages; pageNumber += 1) {
            if (cancelled) {
              break;
            }

            const page = await pdf.getPage(pageNumber);
            if (cancelled) {
              page.cleanup();
              break;
            }

            const baseViewport = page.getViewport({ scale: 1 });
            const availableWidth = Math.max(viewerWidth - 40, 280);
            const scale = availableWidth / baseViewport.width;
            const viewport = page.getViewport({ scale });
            const outputScale = Math.max(window.devicePixelRatio || 1, 1);
            if (outlineSections.length === 0) {
              const textContent = await page.getTextContent().catch(() => null);
              if (textContent) {
                for (const line of textItemsToLines(textContent.items)) {
                  const section = sectionFromLine(line, pageNumber, baseViewport.height);
                  if (section) {
                    textSections.push(section);
                  }
                }
              }
            }

            const pageElement = document.createElement("div");
            pageElement.className = "pdf-document-page";
            pageElements.set(pageNumber, pageElement);
            pageHeights.set(pageNumber, Math.floor(viewport.height));

            const canvas = document.createElement("canvas");
            canvas.className = "pdf-document-canvas";
            canvas.setAttribute("aria-label", `${title} page ${pageNumber}`);
            canvas.width = Math.floor(viewport.width * outputScale);
            canvas.height = Math.floor(viewport.height * outputScale);
            canvas.style.width = `${Math.floor(viewport.width)}px`;
            canvas.style.height = `${Math.floor(viewport.height)}px`;

            const context = canvas.getContext("2d");
            if (!context) {
              throw new Error("Canvas rendering is unavailable in this browser.");
            }

            pageElement.appendChild(canvas);
            container.appendChild(pageElement);

            const renderTask = page.render({
              canvas,
              canvasContext: context,
              viewport,
              transform: outputScale !== 1 ? [outputScale, 0, 0, outputScale, 0, 0] : undefined,
              background: "#ffffff",
            });
            renderTasks.push(renderTask);
            await renderTask.promise;
            page.cleanup();
          }
          if (!cancelled) {
            const sections =
              outlineSections.length > 0 ? outlineSections : finalizeSections(textSections);
            attachSectionAnchors(sections, pageElements, pageHeights, sectionAnchorsRef.current);
            onSectionsChangeRef.current?.(sections);
          }
        } catch (renderError) {
          if (cancelled) {
            return;
          }
          setStatus("error");
          setError(renderError instanceof Error ? renderError.message : "PDF could not be loaded.");
          onSectionsChangeRef.current?.([]);
        }
      })();

      return () => {
        cancelled = true;
        for (const task of renderTasks) {
          task.cancel();
        }
        void loadingTask.destroy();
      };
    }, [title, url, viewerWidth]);

    const viewerClassName = `pdf-document-viewer${className ? ` ${className}` : ""}`;

    return (
      <div ref={scrollRef} className={viewerClassName} aria-label={title}>
        <div ref={pagesRef} className="pdf-document-pages" />
        {status === "loading" ? <div className="paper-pdf-modal-state">Loading PDF...</div> : null}
        {status === "error" ? <div className="paper-pdf-modal-state error">{error}</div> : null}
      </div>
    );
  },
);
