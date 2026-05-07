import { createRef } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PdfDocumentViewer, type PdfDocumentViewerHandle } from "./PdfDocumentViewer";

const pdfMocks = vi.hoisted(() => ({
  getDocument: vi.fn(),
  renderCancel: vi.fn(),
  loadingDestroy: vi.fn(),
  pageCleanup: vi.fn(),
}));

vi.mock("pdfjs-dist/legacy/build/pdf.mjs", () => ({
  getDocument: pdfMocks.getDocument,
  GlobalWorkerOptions: {},
  TextLayer: class {
    container: HTMLElement;

    constructor({ container }: { container: HTMLElement }) {
      this.container = container;
    }

    async render() {
      const span = document.createElement("span");
      span.textContent = "Selected method text";
      this.container.appendChild(span);
    }
  },
}));

vi.mock("pdfjs-dist/legacy/build/pdf.worker.mjs?url", () => ({
  default: "/pdf.worker.mjs",
}));

function firePdfPointer(
  element: Element,
  type: "pointerdown" | "pointermove" | "pointerup",
  options: { clientX: number; clientY: number; pointerId: number },
) {
  const event = new Event(type, { bubbles: true, cancelable: true });
  Object.defineProperties(event, {
    clientX: { value: options.clientX },
    clientY: { value: options.clientY },
    pointerId: { value: options.pointerId },
  });
  fireEvent(element, event);
}

describe("PdfDocumentViewer", () => {
  const resizeCallbacks: ResizeObserverCallback[] = [];
  const originalResizeObserver = globalThis.ResizeObserver;
  const originalScrollIntoView = HTMLElement.prototype.scrollIntoView;
  const scrollIntoView = vi.fn();
  let elementWidth = 640;

  beforeEach(() => {
    vi.clearAllMocks();
    scrollIntoView.mockClear();
    resizeCallbacks.length = 0;
    elementWidth = 640;
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: scrollIntoView,
    });
    globalThis.ResizeObserver = class {
      callback: ResizeObserverCallback;

      constructor(callback: ResizeObserverCallback) {
        this.callback = callback;
        resizeCallbacks.push(callback);
      }

      observe() {}
      disconnect() {}
      unobserve() {}
    };
    Object.defineProperty(HTMLElement.prototype, "getBoundingClientRect", {
      configurable: true,
      value: () => ({
        bottom: 480,
        height: 480,
        left: 0,
        right: elementWidth,
        top: 0,
        width: elementWidth,
        x: 0,
        y: 0,
        toJSON: () => ({}),
      }),
    });
    Object.defineProperty(HTMLCanvasElement.prototype, "getContext", {
      configurable: true,
      value: () => ({ drawImage: vi.fn() }),
    });
    Object.defineProperty(HTMLCanvasElement.prototype, "toDataURL", {
      configurable: true,
      value: () => "data:image/png;base64,ZmFrZS1jcm9w",
    });
    pdfMocks.getDocument.mockReturnValue({
      destroy: pdfMocks.loadingDestroy,
      promise: Promise.resolve({
        numPages: 1,
        getOutline: vi.fn().mockResolvedValue(null),
        getDestination: vi.fn(),
        getPageIndex: vi.fn(),
        getPage: vi.fn().mockResolvedValue({
          cleanup: pdfMocks.pageCleanup,
          getTextContent: vi.fn().mockResolvedValue({
            items: [
              { str: "Abstract", transform: [1, 0, 0, 1, 40, 760] },
              { str: "This is body copy.", transform: [1, 0, 0, 1, 40, 730] },
              { str: "1 Introduction", transform: [1, 0, 0, 1, 40, 700] },
              { str: "2 Method", transform: [1, 0, 0, 1, 40, 650] },
            ],
          }),
          getViewport: ({ scale }: { scale: number }) => ({
            width: 600 * scale,
            height: 800 * scale,
          }),
          render: vi.fn().mockReturnValue({
            cancel: pdfMocks.renderCancel,
            promise: Promise.resolve(),
          }),
        }),
      }),
    });
  });

  afterEach(() => {
    globalThis.ResizeObserver = originalResizeObserver;
    if (originalScrollIntoView) {
      Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
        configurable: true,
        value: originalScrollIntoView,
      });
    } else {
      delete (HTMLElement.prototype as { scrollIntoView?: unknown }).scrollIntoView;
    }
  });

  it("does not restart rendering when scrollbar layout triggers a same-width resize", async () => {
    render(<PdfDocumentViewer url="https://example.test/paper.pdf" title="Paper PDF" />);

    await waitFor(() => {
      expect(pdfMocks.getDocument).toHaveBeenCalledTimes(1);
    });

    act(() => {
      resizeCallbacks[0]?.([] as unknown as ResizeObserverEntry[], {} as ResizeObserver);
      resizeCallbacks[0]?.([] as unknown as ResizeObserverEntry[], {} as ResizeObserver);
    });

    await waitFor(() => {
      expect(pdfMocks.getDocument).toHaveBeenCalledTimes(1);
    });
    expect(pdfMocks.loadingDestroy).not.toHaveBeenCalled();
  });

  it("does not restart rendering when scrollbar layout changes the measured width", async () => {
    render(<PdfDocumentViewer url="https://example.test/paper.pdf" title="Paper PDF" />);

    await waitFor(() => {
      expect(pdfMocks.getDocument).toHaveBeenCalledTimes(1);
    });

    act(() => {
      elementWidth = 627;
      resizeCallbacks[0]?.([] as unknown as ResizeObserverEntry[], {} as ResizeObserver);
    });

    await waitFor(() => {
      expect(pdfMocks.getDocument).toHaveBeenCalledTimes(1);
    });
    expect(pdfMocks.loadingDestroy).not.toHaveBeenCalled();
  });

  it("restarts rendering when the viewer has a material layout resize", async () => {
    render(<PdfDocumentViewer url="https://example.test/paper.pdf" title="Paper PDF" />);

    await waitFor(() => {
      expect(pdfMocks.getDocument).toHaveBeenCalledTimes(1);
    });

    act(() => {
      elementWidth = 560;
      resizeCallbacks[0]?.([] as unknown as ResizeObserverEntry[], {} as ResizeObserver);
    });

    await waitFor(() => {
      expect(pdfMocks.getDocument).toHaveBeenCalledTimes(2);
    });
    expect(pdfMocks.loadingDestroy).toHaveBeenCalled();
  });

  it("reports detected paper sections from text headings", async () => {
    const onSectionsChange = vi.fn();

    render(
      <PdfDocumentViewer
        url="https://example.test/paper.pdf"
        title="Paper PDF"
        onSectionsChange={onSectionsChange}
      />,
    );

    await waitFor(() => {
      expect(onSectionsChange).toHaveBeenCalledWith([
        expect.objectContaining({
          id: "abstract-1",
          title: "Abstract",
          pageNumber: 1,
          pageYOffsetRatio: 0.05,
        }),
        expect.objectContaining({
          id: "introduction-1",
          title: "Introduction",
          pageNumber: 1,
          pageYOffsetRatio: 0.125,
        }),
        expect.objectContaining({
          id: "method-1",
          title: "Method",
          pageNumber: 1,
          pageYOffsetRatio: 0.1875,
        }),
      ]);
    });
  });

  it("scrolls to known section anchors and ignores unknown sections", async () => {
    const viewerRef = createRef<PdfDocumentViewerHandle>();
    const onSectionsChange = vi.fn();

    render(
      <PdfDocumentViewer
        ref={viewerRef}
        url="https://example.test/paper.pdf"
        title="Paper PDF"
        onSectionsChange={onSectionsChange}
      />,
    );

    await waitFor(() => {
      expect(onSectionsChange).toHaveBeenCalled();
    });

    const anchors = document.querySelectorAll<HTMLElement>(".pdf-document-section-anchor");
    expect(anchors).toHaveLength(3);
    expect(anchors[1]?.style.top).toBe("100px");

    act(() => {
      viewerRef.current?.scrollToSection("introduction-1");
    });
    expect(scrollIntoView).toHaveBeenCalledWith({ behavior: "smooth", block: "start" });

    const callCount = scrollIntoView.mock.calls.length;
    act(() => {
      viewerRef.current?.scrollToSection("missing-section");
    });
    expect(scrollIntoView).toHaveBeenCalledTimes(callCount);
  });

  it("emits selected text actions from the PDF text layer", async () => {
    const onSelectionAction = vi.fn();

    render(
      <PdfDocumentViewer
        url="https://example.test/paper.pdf"
        title="Paper PDF"
        paperId="arxiv:2401.12345"
        onSelectionAction={onSelectionAction}
      />,
    );

    await waitFor(() => {
      expect(document.querySelector(".pdf-document-text-layer span")).toBeTruthy();
    });

    const selectedNode = document.querySelector(".pdf-document-text-layer span")!;
    const range = {
      getBoundingClientRect: () => ({
        bottom: 140,
        height: 20,
        left: 80,
        right: 260,
        top: 120,
        width: 180,
        x: 80,
        y: 120,
        toJSON: () => ({}),
      }),
    } as Range;
    const selection = {
      anchorNode: selectedNode.firstChild,
      isCollapsed: false,
      rangeCount: 1,
      getRangeAt: () => range,
      removeAllRanges: vi.fn(),
      toString: () => "Selected method text",
    } as unknown as Selection;
    const originalSelection = window.getSelection;
    Object.defineProperty(window, "getSelection", {
      configurable: true,
      value: () => selection,
    });

    try {
      fireEvent.mouseUp(document.querySelector(".pdf-document-viewer")!);
      const explain = await screen.findByRole("button", { name: "Explain" });
      fireEvent.click(explain);
    } finally {
      Object.defineProperty(window, "getSelection", {
        configurable: true,
        value: originalSelection,
      });
    }

    expect(onSelectionAction).toHaveBeenCalledWith(
      expect.objectContaining({
        action: "explain",
        paper_id: "arxiv:2401.12345",
        page_number: 1,
        selected_text: "Selected method text",
        nearby_text: expect.stringContaining("Abstract"),
      }),
    );
  });

  it("emits pen region actions with normalized points", async () => {
    const onSelectionAction = vi.fn();

    render(
      <PdfDocumentViewer
        url="https://example.test/paper.pdf"
        title="Paper PDF"
        paperId="arxiv:2401.12345"
        toolMode="pen"
        onSelectionAction={onSelectionAction}
      />,
    );

    await waitFor(() => {
      expect(document.querySelector(".pdf-document-page")).toBeTruthy();
    });

    const viewer = document.querySelector(".pdf-document-viewer")!;
    const page = document.querySelector(".pdf-document-page")!;
    firePdfPointer(page, "pointerdown", { clientX: 80, clientY: 120, pointerId: 1 });
    firePdfPointer(viewer, "pointermove", { clientX: 240, clientY: 260, pointerId: 1 });
    firePdfPointer(viewer, "pointerup", { clientX: 240, clientY: 260, pointerId: 1 });

    const trace = await screen.findByRole("button", { name: "Trace" });
    fireEvent.click(trace);

    expect(onSelectionAction).toHaveBeenCalledWith(
      expect.objectContaining({
        action: "trace",
        paper_id: "arxiv:2401.12345",
        region: expect.objectContaining({
          image_base64: "ZmFrZS1jcm9w",
          content_type: "image/png",
          points: expect.arrayContaining([
            expect.objectContaining({ x: expect.any(Number), y: expect.any(Number) }),
          ]),
        }),
      }),
    );
  });
});
