import { useEffect, useRef, useState } from "react";
import {
  getDocument,
  GlobalWorkerOptions,
  type PDFDocumentLoadingTask,
  type RenderTask,
} from "pdfjs-dist/legacy/build/pdf.mjs";
import pdfWorkerUrl from "pdfjs-dist/legacy/build/pdf.worker.mjs?url";

GlobalWorkerOptions.workerSrc = pdfWorkerUrl;

type PdfDocumentViewerProps = {
  url: string;
  title: string;
  className?: string;
};

type PdfViewerStatus = "loading" | "ready" | "error";

export function PdfDocumentViewer({ url, title, className = "" }: PdfDocumentViewerProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const pagesRef = useRef<HTMLDivElement | null>(null);
  const [viewerWidth, setViewerWidth] = useState(0);
  const [status, setStatus] = useState<PdfViewerStatus>("loading");
  const [error, setError] = useState("");

  useEffect(() => {
    const element = scrollRef.current;
    if (!element) {
      return;
    }

    const updateWidth = () => {
      setViewerWidth(Math.floor(element.clientWidth));
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
    setError("");

    if (!url) {
      setStatus("error");
      setError("No PDF URL is available.");
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

          const pageElement = document.createElement("div");
          pageElement.className = "pdf-document-page";

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
      } catch (renderError) {
        if (cancelled) {
          return;
        }
        setStatus("error");
        setError(renderError instanceof Error ? renderError.message : "PDF could not be loaded.");
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
}
