import { act, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PdfDocumentViewer } from "./PdfDocumentViewer";

const pdfMocks = vi.hoisted(() => ({
  getDocument: vi.fn(),
  renderCancel: vi.fn(),
  loadingDestroy: vi.fn(),
  pageCleanup: vi.fn(),
}));

vi.mock("pdfjs-dist/legacy/build/pdf.mjs", () => ({
  getDocument: pdfMocks.getDocument,
  GlobalWorkerOptions: {},
}));

vi.mock("pdfjs-dist/legacy/build/pdf.worker.mjs?url", () => ({
  default: "/pdf.worker.mjs",
}));

describe("PdfDocumentViewer", () => {
  const resizeCallbacks: ResizeObserverCallback[] = [];
  const originalResizeObserver = globalThis.ResizeObserver;

  beforeEach(() => {
    vi.clearAllMocks();
    resizeCallbacks.length = 0;
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
        right: 640,
        top: 0,
        width: 640,
        x: 0,
        y: 0,
        toJSON: () => ({}),
      }),
    });
    Object.defineProperty(HTMLCanvasElement.prototype, "getContext", {
      configurable: true,
      value: () => ({}),
    });
    pdfMocks.getDocument.mockReturnValue({
      destroy: pdfMocks.loadingDestroy,
      promise: Promise.resolve({
        numPages: 1,
        getPage: vi.fn().mockResolvedValue({
          cleanup: pdfMocks.pageCleanup,
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
});
