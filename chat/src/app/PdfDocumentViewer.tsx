import {
  Fragment,
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
} from "react";
import {
  getDocument,
  GlobalWorkerOptions,
  TextLayer,
  type PDFDocumentLoadingTask,
  type RenderTask,
} from "pdfjs-dist/legacy/build/pdf.mjs";
import pdfWorkerUrl from "pdfjs-dist/legacy/build/pdf.worker.mjs?url";
import type { PaperSelectionAction, PaperSelectionPayload } from "../types";
import type {
  PaperAnnotationRect,
  PaperAnnotationTarget,
  PaperExplorationAnnotation,
} from "./appTypes";

GlobalWorkerOptions.workerSrc = pdfWorkerUrl;

export type PdfDocumentSection = {
  id: string;
  title: string;
  pageNumber: number;
  pageYOffsetRatio: number;
};

export type PdfDocumentViewerHandle = {
  scrollToSection: (sectionId: string) => void;
  scrollToAnnotation: (annotationId: string) => void;
  clearSelection: () => void;
};

type PdfDocumentToolMode = "select" | "pen";

type PdfDocumentViewerProps = {
  url: string;
  title: string;
  paperId?: string;
  className?: string;
  toolMode?: PdfDocumentToolMode;
  annotations?: PaperExplorationAnnotation[];
  annotationsHidden?: boolean;
  onSectionsChange?: (sections: PdfDocumentSection[]) => void;
  onSelectionAction?: (selection: PaperSelectionPayload) => void;
  onAddNote?: (target: PaperAnnotationTarget) => void;
};

type PdfViewerStatus = "loading" | "ready" | "error";

const VIEWER_RELAYOUT_WIDTH_THRESHOLD = 24;

type NormalizedPoint = { x: number; y: number };

type PageBox = { left: number; top: number; width: number; height: number };

type PenStroke = {
  pageNumber: number;
  points: NormalizedPoint[];
  pageBox: PageBox;
};

type TextSelectionHighlight = {
  pageNumber: number;
  rects: PaperAnnotationRect[];
};

type SelectionPopoverState = {
  left: number;
  top: number;
  payload: Omit<PaperSelectionPayload, "action">;
  noteTarget: PaperAnnotationTarget;
};

const SELECTION_ACTIONS: Array<{ action: PaperSelectionAction; label: string }> = [
  { action: "explain", label: "Explain" },
  { action: "trace", label: "Trace" },
  { action: "challenge", label: "Challenge" },
  { action: "reproduce", label: "Reproduce" },
];

function clampPageYOffsetRatio(value: number) {
  if (!Number.isFinite(value)) {
    return 0;
  }
  return Math.max(0, Math.min(1, value));
}

function clampUnit(value: number) {
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

function bboxForPoints(points: NormalizedPoint[]) {
  const xs = points.map((point) => point.x);
  const ys = points.map((point) => point.y);
  const x = clampUnit(Math.min(...xs));
  const y = clampUnit(Math.min(...ys));
  const right = clampUnit(Math.max(...xs));
  const bottom = clampUnit(Math.max(...ys));
  return {
    x,
    y,
    width: clampUnit(right - x),
    height: clampUnit(bottom - y),
  };
}

function pageNumberFromElement(element: Element | null) {
  const page = element?.closest<HTMLElement>(".pdf-document-page");
  const pageNumber = Number(page?.dataset.pageNumber);
  return Number.isFinite(pageNumber) && pageNumber > 0 ? Math.floor(pageNumber) : 1;
}

function textForPage(linesByPage: Map<number, string[]>, pageNumber: number) {
  return (linesByPage.get(pageNumber) ?? []).join(" ").replace(/\s+/g, " ").trim();
}

function normalizeRectWithinPage(
  rect: DOMRect,
  pageElement: HTMLElement,
): PaperAnnotationRect | null {
  const pageRect = pageElement.getBoundingClientRect();
  if (pageRect.width <= 0 || pageRect.height <= 0 || rect.width <= 0 || rect.height <= 0) {
    return null;
  }
  return {
    x: clampUnit((rect.left - pageRect.left) / pageRect.width),
    y: clampUnit((rect.top - pageRect.top) / pageRect.height),
    width: clampUnit(rect.width / pageRect.width),
    height: clampUnit(rect.height / pageRect.height),
  };
}

function cropCanvasRegion(
  canvas: HTMLCanvasElement | undefined,
  bbox: ReturnType<typeof bboxForPoints>,
) {
  if (!canvas || bbox.width <= 0 || bbox.height <= 0) {
    return null;
  }
  try {
    const sourceX = Math.floor(bbox.x * canvas.width);
    const sourceY = Math.floor(bbox.y * canvas.height);
    const sourceWidth = Math.max(1, Math.floor(bbox.width * canvas.width));
    const sourceHeight = Math.max(1, Math.floor(bbox.height * canvas.height));
    const maxSide = 640;
    const scale = Math.min(1, maxSide / Math.max(sourceWidth, sourceHeight));
    const targetWidth = Math.max(1, Math.floor(sourceWidth * scale));
    const targetHeight = Math.max(1, Math.floor(sourceHeight * scale));
    const crop = document.createElement("canvas");
    crop.width = targetWidth;
    crop.height = targetHeight;
    const context = crop.getContext("2d");
    if (!context) {
      return null;
    }
    context.drawImage(
      canvas,
      sourceX,
      sourceY,
      sourceWidth,
      sourceHeight,
      0,
      0,
      targetWidth,
      targetHeight,
    );
    const dataUrl = crop.toDataURL("image/png");
    const marker = "base64,";
    const markerIndex = dataUrl.indexOf(marker);
    return markerIndex >= 0 ? dataUrl.slice(markerIndex + marker.length) : null;
  } catch {
    return null;
  }
}

function pointerIdForEvent(event: PointerEvent) {
  return Number.isFinite(event.pointerId) ? event.pointerId : 1;
}

export const PdfDocumentViewer = forwardRef<PdfDocumentViewerHandle, PdfDocumentViewerProps>(
  function PdfDocumentViewer(
    {
      url,
      title,
      paperId,
      className = "",
      toolMode = "select",
      annotations = [],
      annotationsHidden = false,
      onSectionsChange,
      onSelectionAction,
      onAddNote,
    },
    ref,
  ) {
    const scrollRef = useRef<HTMLDivElement | null>(null);
    const pagesRef = useRef<HTMLDivElement | null>(null);
    const onSectionsChangeRef = useRef(onSectionsChange);
    const onSelectionActionRef = useRef(onSelectionAction);
    const onAddNoteRef = useRef(onAddNote);
    const sectionAnchorsRef = useRef(new Map<string, HTMLElement>());
    const pageTextRef = useRef(new Map<number, string[]>());
    const pageCanvasesRef = useRef(new Map<number, HTMLCanvasElement>());
    const latestTextSelectionRef = useRef<Omit<PaperSelectionPayload, "action"> | null>(null);
    const drawingPointerIdRef = useRef<number | null>(null);
    const drawingPageElementRef = useRef<HTMLElement | null>(null);
    const activeStrokeRef = useRef<PenStroke | null>(null);
    const selectionPopoverRef = useRef<SelectionPopoverState | null>(null);
    const [viewerWidth, setViewerWidth] = useState(0);
    const [status, setStatus] = useState<PdfViewerStatus>("loading");
    const [error, setError] = useState("");
    const [selectionPopover, setSelectionPopover] = useState<SelectionPopoverState | null>(null);
    const [activeTextHighlight, setActiveTextHighlight] = useState<TextSelectionHighlight | null>(
      null,
    );
    const [activeStroke, setActiveStroke] = useState<PenStroke | null>(null);
    const [completedStroke, setCompletedStroke] = useState<PenStroke | null>(null);
    const [pageBoxes, setPageBoxes] = useState<Record<number, PageBox>>({});

    const clearSelection = () => {
      latestTextSelectionRef.current = null;
      drawingPointerIdRef.current = null;
      drawingPageElementRef.current = null;
      activeStrokeRef.current = null;
      selectionPopoverRef.current = null;
      setSelectionPopover(null);
      setActiveTextHighlight(null);
      setActiveStroke(null);
      setCompletedStroke(null);
      window.getSelection()?.removeAllRanges();
    };

    useImperativeHandle(
      ref,
      () => ({
        scrollToSection: (sectionId: string) => {
          sectionAnchorsRef.current.get(sectionId)?.scrollIntoView({
            behavior: "smooth",
            block: "start",
          });
        },
        scrollToAnnotation: (annotationId: string) => {
          const safeAnnotationId = annotationId.replace(/"/g, '\\"');
          scrollRef.current
            ?.querySelector<HTMLElement>(`[data-annotation-id="${safeAnnotationId}"]`)
            ?.scrollIntoView({
              behavior: "smooth",
              block: "center",
            });
        },
        clearSelection,
      }),
      [],
    );

    useEffect(() => {
      onSectionsChangeRef.current = onSectionsChange;
    }, [onSectionsChange]);

    useEffect(() => {
      onSelectionActionRef.current = onSelectionAction;
    }, [onSelectionAction]);

    useEffect(() => {
      onAddNoteRef.current = onAddNote;
    }, [onAddNote]);

    useEffect(() => {
      selectionPopoverRef.current = selectionPopover;
    }, [selectionPopover]);

    useEffect(() => {
      const element = scrollRef.current;
      if (!element) {
        return;
      }

      const updateWidth = () => {
        const nextWidth = Math.floor(element.getBoundingClientRect().width);
        setViewerWidth((currentWidth) =>
          currentWidth === nextWidth ||
          (currentWidth > 0 &&
            Math.abs(currentWidth - nextWidth) <= VIEWER_RELAYOUT_WIDTH_THRESHOLD)
            ? currentWidth
            : nextWidth,
        );
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
      pageTextRef.current.clear();
      pageCanvasesRef.current.clear();
      latestTextSelectionRef.current = null;
      drawingPointerIdRef.current = null;
      drawingPageElementRef.current = null;
      activeStrokeRef.current = null;
      setSelectionPopover(null);
      setActiveTextHighlight(null);
      setActiveStroke(null);
      setCompletedStroke(null);
      setPageBoxes({});
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
          const nextPageBoxes: Record<number, PageBox> = {};

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
                const lines = textItemsToLines(textContent.items);
                pageTextRef.current.set(
                  pageNumber,
                  lines.map((line) => line.text),
                );
                for (const line of lines) {
                  const section = sectionFromLine(line, pageNumber, baseViewport.height);
                  if (section) {
                    textSections.push(section);
                  }
                }
              }
            } else {
              const textContent = await page.getTextContent().catch(() => null);
              if (textContent) {
                pageTextRef.current.set(
                  pageNumber,
                  textItemsToLines(textContent.items).map((line) => line.text),
                );
              }
            }

            const pageElement = document.createElement("div");
            pageElement.className = "pdf-document-page";
            pageElement.dataset.pageNumber = String(pageNumber);
            pageElements.set(pageNumber, pageElement);
            pageHeights.set(pageNumber, Math.floor(viewport.height));

            const canvas = document.createElement("canvas");
            canvas.className = "pdf-document-canvas";
            canvas.setAttribute("aria-label", `${title} page ${pageNumber}`);
            canvas.width = Math.floor(viewport.width * outputScale);
            canvas.height = Math.floor(viewport.height * outputScale);
            canvas.style.width = `${Math.floor(viewport.width)}px`;
            canvas.style.height = `${Math.floor(viewport.height)}px`;
            pageCanvasesRef.current.set(pageNumber, canvas);

            const context = canvas.getContext("2d");
            if (!context) {
              throw new Error("Canvas rendering is unavailable in this browser.");
            }

            pageElement.appendChild(canvas);
            const textLayer = document.createElement("div");
            textLayer.className = "pdf-document-text-layer";
            textLayer.style.width = `${Math.floor(viewport.width)}px`;
            textLayer.style.height = `${Math.floor(viewport.height)}px`;
            pageElement.appendChild(textLayer);
            container.appendChild(pageElement);
            nextPageBoxes[pageNumber] = strokePageBox(pageElement);

            const renderTask = page.render({
              canvas,
              canvasContext: context,
              viewport,
              transform: outputScale !== 1 ? [outputScale, 0, 0, outputScale, 0, 0] : undefined,
              background: "#ffffff",
            });
            renderTasks.push(renderTask);
            await renderTask.promise;
            const textContent = await page.getTextContent().catch(() => null);
            if (textContent) {
              await new TextLayer({
                textContentSource: textContent,
                container: textLayer,
                viewport,
              }).render();
            }
            page.cleanup();
          }
          if (!cancelled) {
            const sections =
              outlineSections.length > 0 ? outlineSections : finalizeSections(textSections);
            attachSectionAnchors(sections, pageElements, pageHeights, sectionAnchorsRef.current);
            setPageBoxes(nextPageBoxes);
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

    const buildTextSelectionPayload = (selection: Selection) => {
      if (!paperId || selection.isCollapsed) {
        return null;
      }
      const selectedText = selection.toString().replace(/\s+/g, " ").trim();
      if (!selectedText) {
        return null;
      }
      const range = selection.rangeCount > 0 ? selection.getRangeAt(0) : null;
      const anchorElement =
        selection.anchorNode instanceof Element
          ? selection.anchorNode
          : (selection.anchorNode?.parentElement ?? null);
      const pageElement = anchorElement?.closest<HTMLElement>(".pdf-document-page") ?? null;
      const pageNumber = pageNumberFromElement(pageElement);
      const rect = range?.getBoundingClientRect();
      if (!rect || rect.width <= 0 || rect.height <= 0 || !pageElement) {
        return null;
      }
      const rangeRects =
        typeof range.getClientRects === "function" ? Array.from(range.getClientRects()) : [];
      const normalizedRects = (rangeRects.length > 0 ? rangeRects : [rect])
        .map((candidate) => normalizeRectWithinPage(candidate, pageElement))
        .filter((candidate): candidate is PaperAnnotationRect => Boolean(candidate));
      if (normalizedRects.length === 0) {
        return null;
      }
      const root = scrollRef.current;
      const rootRect = root?.getBoundingClientRect();
      const nearbyText = textForPage(pageTextRef.current, pageNumber);
      return {
        left: rect.left + rect.width / 2 - (rootRect?.left ?? 0) + (root?.scrollLeft ?? 0),
        top: Math.max(8, rect.top - 48 - (rootRect?.top ?? 0) + (root?.scrollTop ?? 0)),
        payload: {
          paper_id: paperId,
          page_number: pageNumber,
          selected_text: selectedText,
          nearby_text: nearbyText,
        },
        noteTarget: {
          paperId,
          pageNumber,
          selectedText,
          nearbyText,
          rects: normalizedRects,
        },
      } satisfies SelectionPopoverState;
    };

    const handleMouseUp = (_event: ReactMouseEvent<HTMLDivElement>) => {
      if (toolMode !== "select") {
        return;
      }
      const selection = window.getSelection();
      if (!selection) {
        return;
      }
      const payload = buildTextSelectionPayload(selection);
      if (!payload) {
        return;
      }
      latestTextSelectionRef.current = payload.payload;
      setCompletedStroke(null);
      setActiveTextHighlight({
        pageNumber: payload.noteTarget.pageNumber,
        rects: payload.noteTarget.rects ?? [],
      });
      setSelectionPopover(payload);
      window.getSelection()?.removeAllRanges();
    };

    const pointFromPointerEvent = (
      event: PointerEvent,
      pageElement: HTMLElement,
    ): NormalizedPoint => {
      const pageRect = pageElement.getBoundingClientRect();
      return {
        x: clampUnit((event.clientX - pageRect.left) / pageRect.width),
        y: clampUnit((event.clientY - pageRect.top) / pageRect.height),
      };
    };

    const strokePageBox = (pageElement: HTMLElement) => {
      const pageRect = pageElement.getBoundingClientRect();
      const rootRect = scrollRef.current?.getBoundingClientRect();
      const root = scrollRef.current;
      return {
        left: pageRect.left - (rootRect?.left ?? 0) + (root?.scrollLeft ?? 0),
        top: pageRect.top - (rootRect?.top ?? 0) + (root?.scrollTop ?? 0),
        width: pageRect.width,
        height: pageRect.height,
      };
    };

    const handlePointerDown = (event: PointerEvent) => {
      if (toolMode !== "pen" || !paperId) {
        return;
      }
      const target = event.target instanceof Element ? event.target : null;
      const pageElement = target?.closest<HTMLElement>(".pdf-document-page");
      if (!pageElement) {
        return;
      }
      event.preventDefault();
      const pointerId = pointerIdForEvent(event);
      scrollRef.current?.setPointerCapture?.(pointerId);
      drawingPointerIdRef.current = pointerId;
      drawingPageElementRef.current = pageElement;
      const pageNumber = pageNumberFromElement(pageElement);
      const stroke: PenStroke = {
        pageNumber,
        points: [pointFromPointerEvent(event, pageElement)],
        pageBox: strokePageBox(pageElement),
      };
      setCompletedStroke(null);
      setSelectionPopover(null);
      activeStrokeRef.current = stroke;
      setActiveStroke(stroke);
    };

    const handlePointerMove = (event: PointerEvent) => {
      if (toolMode !== "pen" || drawingPointerIdRef.current !== pointerIdForEvent(event)) {
        return;
      }
      const pageElement = drawingPageElementRef.current;
      if (!pageElement) {
        return;
      }
      event.preventDefault();
      const current = activeStrokeRef.current;
      if (!current) {
        return;
      }
      const nextStroke = {
        ...current,
        points: [...current.points, pointFromPointerEvent(event, pageElement)],
      };
      activeStrokeRef.current = nextStroke;
      setActiveStroke(nextStroke);
    };

    const finishPenSelection = (event: PointerEvent) => {
      const pointerId = pointerIdForEvent(event);
      if (toolMode !== "pen" || drawingPointerIdRef.current !== pointerId) {
        return;
      }
      drawingPointerIdRef.current = null;
      drawingPageElementRef.current = null;
      scrollRef.current?.releasePointerCapture?.(pointerId);
      const current = activeStrokeRef.current;
      activeStrokeRef.current = null;
      setActiveStroke(null);
      if (!current || current.points.length < 2 || !paperId) {
        return;
      }
      const bbox = bboxForPoints(current.points);
      if (bbox.width < 0.01 || bbox.height < 0.01) {
        return;
      }
      const canvas = pageCanvasesRef.current.get(current.pageNumber);
      const imageBase64 = cropCanvasRegion(canvas, bbox);
      const linkedText = latestTextSelectionRef.current;
      const payload: Omit<PaperSelectionPayload, "action"> = {
        paper_id: paperId,
        page_number: current.pageNumber,
        selected_text: linkedText?.selected_text ?? null,
        nearby_text:
          linkedText?.page_number === current.pageNumber
            ? linkedText.nearby_text
            : textForPage(pageTextRef.current, current.pageNumber),
        section_title: linkedText?.section_title ?? null,
        region: {
          points: current.points,
          bbox,
          image_base64: imageBase64,
          content_type: imageBase64 ? "image/png" : null,
        },
      };
      setCompletedStroke(current);
      setSelectionPopover({
        left: current.pageBox.left + (bbox.x + bbox.width / 2) * current.pageBox.width,
        top: Math.max(8, current.pageBox.top + bbox.y * current.pageBox.height - 44),
        payload,
        noteTarget: {
          paperId,
          pageNumber: current.pageNumber,
          selectedText: payload.selected_text,
          nearbyText: payload.nearby_text,
          sectionTitle: payload.section_title,
          region: {
            points: current.points,
            bbox,
          },
        },
      });
    };

    useEffect(() => {
      const element = scrollRef.current;
      if (!element) {
        return;
      }
      element.addEventListener("pointerdown", handlePointerDown);
      element.addEventListener("pointermove", handlePointerMove);
      element.addEventListener("pointerup", finishPenSelection);
      element.addEventListener("pointercancel", finishPenSelection);
      return () => {
        element.removeEventListener("pointerdown", handlePointerDown);
        element.removeEventListener("pointermove", handlePointerMove);
        element.removeEventListener("pointerup", finishPenSelection);
        element.removeEventListener("pointercancel", finishPenSelection);
      };
    });

    useEffect(() => {
      const handleKeyDown = (event: KeyboardEvent) => {
        if (event.key === "Escape" && selectionPopoverRef.current) {
          clearSelection();
        }
      };
      const handlePointerDown = (event: PointerEvent) => {
        if (!selectionPopoverRef.current) {
          return;
        }
        const target = event.target instanceof Element ? event.target : null;
        if (target?.closest(".pdf-document-selection-popover")) {
          return;
        }
        clearSelection();
      };
      document.addEventListener("keydown", handleKeyDown);
      document.addEventListener("pointerdown", handlePointerDown, true);
      return () => {
        document.removeEventListener("keydown", handleKeyDown);
        document.removeEventListener("pointerdown", handlePointerDown, true);
      };
    }, []);

    const handleSelectionAction = (action: PaperSelectionAction) => {
      if (!selectionPopover) {
        return;
      }
      onSelectionActionRef.current?.({
        ...selectionPopover.payload,
        action,
      });
      clearSelection();
    };

    const handleAddNote = () => {
      if (!selectionPopover) {
        return;
      }
      onAddNoteRef.current?.(selectionPopover.noteTarget);
      clearSelection();
    };

    const renderStroke = (stroke: PenStroke, className: string) => {
      if (stroke.points.length < 2) {
        return null;
      }
      const polylinePoints = stroke.points
        .map((point) => `${point.x * stroke.pageBox.width},${point.y * stroke.pageBox.height}`)
        .join(" ");
      const isCompleted = className === "completed" && stroke.points.length >= 3;
      const gradientId = `pdf-document-pen-gradient-${className}-${stroke.pageNumber}`;
      const fillId = `pdf-document-pen-fill-${className}-${stroke.pageNumber}`;
      return (
        <svg
          className={`pdf-document-pen-overlay ${className}`}
          aria-hidden="true"
          viewBox={`0 0 ${stroke.pageBox.width} ${stroke.pageBox.height}`}
          preserveAspectRatio="none"
          style={{
            left: stroke.pageBox.left,
            top: stroke.pageBox.top,
            width: stroke.pageBox.width,
            height: stroke.pageBox.height,
          }}
        >
          <defs>
            <linearGradient id={gradientId} x1="0%" y1="0%" x2="100%" y2="100%">
              <stop offset="0%" stopColor="#06b6d4" />
              <stop offset="52%" stopColor="#8b5cf6" />
              <stop offset="100%" stopColor="#f59e0b" />
            </linearGradient>
            <linearGradient id={fillId} x1="0%" y1="0%" x2="100%" y2="100%">
              <stop offset="0%" stopColor="#22d3ee" />
              <stop offset="58%" stopColor="#a78bfa" />
              <stop offset="100%" stopColor="#fbbf24" />
            </linearGradient>
          </defs>
          {isCompleted ? (
            <polygon
              points={polylinePoints}
              className="pdf-document-pen-fill"
              fill={`url(#${fillId})`}
            />
          ) : null}
          <polyline points={polylinePoints} stroke={`url(#${gradientId})`} />
        </svg>
      );
    };

    const renderTextHighlight = (
      highlight: TextSelectionHighlight,
      className: string,
      annotationId?: string,
    ) => {
      const pageBox = pageBoxes[highlight.pageNumber];
      if (!pageBox || highlight.rects.length === 0) {
        return null;
      }
      const anchorTopRatio = Math.min(...highlight.rects.map((rect) => rect.y));
      return (
        <>
          {annotationId ? (
            <span
              className="pdf-document-annotation-anchor"
              data-annotation-id={annotationId}
              style={{
                left: pageBox.left,
                top: pageBox.top + anchorTopRatio * pageBox.height,
              }}
            />
          ) : null}
          {highlight.rects.map((rect, index) => (
            <span
              key={`${highlight.pageNumber}-${index}-${rect.x}-${rect.y}`}
              className={`pdf-document-text-highlight ${className}`}
              style={{
                left: pageBox.left + rect.x * pageBox.width,
                top: pageBox.top + rect.y * pageBox.height,
                width: rect.width * pageBox.width,
                height: rect.height * pageBox.height,
              }}
            />
          ))}
        </>
      );
    };

    const renderAnnotation = (annotation: PaperExplorationAnnotation) => {
      const pageBox = pageBoxes[annotation.pageNumber];
      if (!pageBox) {
        return null;
      }
      if (annotation.kind === "text") {
        return renderTextHighlight(
          { pageNumber: annotation.pageNumber, rects: annotation.rects ?? [] },
          "annotation",
          annotation.annotationId,
        );
      }
      const region = annotation.region;
      if (!region) {
        return null;
      }
      const anchorTop = pageBox.top + region.bbox.y * pageBox.height;
      const polygonPoints = region.points
        .map((point) => `${point.x * pageBox.width},${point.y * pageBox.height}`)
        .join(" ");
      return (
        <>
          <span
            className="pdf-document-annotation-anchor"
            data-annotation-id={annotation.annotationId}
            style={{ left: pageBox.left, top: anchorTop }}
          />
          <svg
            className="pdf-document-annotation-region"
            aria-hidden="true"
            viewBox={`0 0 ${pageBox.width} ${pageBox.height}`}
            preserveAspectRatio="none"
            style={{
              left: pageBox.left,
              top: pageBox.top,
              width: pageBox.width,
              height: pageBox.height,
            }}
          >
            {region.points.length >= 3 ? (
              <polygon points={polygonPoints} />
            ) : (
              <rect
                x={region.bbox.x * pageBox.width}
                y={region.bbox.y * pageBox.height}
                width={region.bbox.width * pageBox.width}
                height={region.bbox.height * pageBox.height}
              />
            )}
          </svg>
        </>
      );
    };

    const viewerClassName = `pdf-document-viewer${toolMode === "pen" ? " pen-mode" : ""}${
      annotationsHidden ? " annotations-hidden" : ""
    }${className ? ` ${className}` : ""}`;

    return (
      <div ref={scrollRef} className={viewerClassName} aria-label={title} onMouseUp={handleMouseUp}>
        <div ref={pagesRef} className="pdf-document-pages" />
        {!annotationsHidden && activeTextHighlight
          ? renderTextHighlight(activeTextHighlight, "active-selection")
          : null}
        {!annotationsHidden
          ? annotations.map((annotation) => (
              <Fragment key={annotation.annotationId}>{renderAnnotation(annotation)}</Fragment>
            ))
          : null}
        {!annotationsHidden && completedStroke ? renderStroke(completedStroke, "completed") : null}
        {!annotationsHidden && activeStroke ? renderStroke(activeStroke, "active") : null}
        {selectionPopover ? (
          <div
            className="pdf-document-selection-popover"
            style={{ left: selectionPopover.left, top: selectionPopover.top }}
          >
            {onAddNote ? (
              <button type="button" onClick={handleAddNote}>
                Add note
              </button>
            ) : null}
            {SELECTION_ACTIONS.map((item) => (
              <button
                key={item.action}
                type="button"
                onClick={() => handleSelectionAction(item.action)}
              >
                {item.label}
              </button>
            ))}
          </div>
        ) : null}
        {status === "loading" ? <div className="paper-pdf-modal-state">Loading PDF...</div> : null}
        {status === "error" ? <div className="paper-pdf-modal-state error">{error}</div> : null}
      </div>
    );
  },
);
