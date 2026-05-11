import { useMemo } from "react";

type ProjectPresentationCanvasProps = {
  visualCode: string;
  imageUrl?: string;
  title?: string;
};

function projectPresentationSrcDoc(visualCode: string) {
  const code = JSON.stringify(visualCode || "");
  return `<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
    <script src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/d3@7/dist/d3.min.js"></script>
    <style>
      *, *::before, *::after { box-sizing: border-box; }
      html, body, #root { width: 100%; height: 100%; margin: 0; overflow: hidden; }
      body { background: #f7f2ea; color: #1b1611; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
      #root { container-type: size; position: relative; }
      #root > * { max-width: 100%; max-height: 100%; }
      svg, canvas { display: block; max-width: 100%; max-height: 100%; overflow: hidden; }
      .presentation-error { padding: 24px; color: #9b3939; font: 14px/1.5 ui-sans-serif, system-ui; }
    </style>
  </head>
  <body>
    <div id="root"></div>
    <script>
      const generatedCode = ${code};
      function showPresentationError(error) {
        const root = document.getElementById("root");
        root.innerHTML = "";
        const node = document.createElement("pre");
        node.className = "presentation-error";
        node.textContent = "Presentation visual failed to render.\\n" + (error && error.message ? error.message : String(error));
        root.appendChild(node);
      }
      const presentationResizeListeners = new Set();
      let presentationResizeFrame = 0;
      function presentationSize() {
        const root = document.getElementById("root");
        const rect = root ? root.getBoundingClientRect() : { width: 0, height: 0 };
        return {
          width: Math.max(1, Math.round(rect.width || window.innerWidth || 1)),
          height: Math.max(1, Math.round(rect.height || window.innerHeight || 1)),
        };
      }
      function notifyPresentationResize() {
        cancelAnimationFrame(presentationResizeFrame);
        presentationResizeFrame = requestAnimationFrame(() => {
          const size = presentationSize();
          for (const listener of presentationResizeListeners) {
            try {
              listener(size);
            } catch (error) {
              console.error(error);
            }
          }
          window.dispatchEvent(new CustomEvent("paperbase:presentation-resize", { detail: size }));
          requestAnimationFrame(() => {
            tightenGeneratedVisual();
            fitGeneratedVisual();
          });
        });
      }
      window.paperbasePresentation = {
        size: presentationSize,
        onResize(listener) {
          if (typeof listener !== "function") return () => {};
          presentationResizeListeners.add(listener);
          listener(presentationSize());
          return () => presentationResizeListeners.delete(listener);
        },
      };
      function svgCoordinateWidth(svg) {
        if (svg && svg.viewBox && svg.viewBox.baseVal && svg.viewBox.baseVal.width) {
          return svg.viewBox.baseVal.width;
        }
        const width = Number(svg && (svg.getAttribute("width") || svg.clientWidth));
        return Number.isFinite(width) && width > 0 ? width : 1000;
      }
      function tightenGeneratedVisual() {
        const root = document.getElementById("root");
        if (!root) return;
        for (const svg of root.querySelectorAll("svg")) {
          svg.style.overflow = "hidden";
          const svgWidth = svgCoordinateWidth(svg);
          for (const text of svg.querySelectorAll("text")) {
            const content = (text.textContent || "").trim();
            const x = Number(text.getAttribute("x") || "0");
            const safeX = Number.isFinite(x) ? Math.max(0, x) : 0;
            const available = Math.max(80, svgWidth - safeX - 28);
            const computed = window.getComputedStyle(text);
            const rawSize = Number(text.getAttribute("font-size") || computed.fontSize.replace("px", ""));
            const maxSize = content.length > 32 ? 22 : content.length > 18 ? 28 : 36;
            if (Number.isFinite(rawSize) && rawSize > maxSize) {
              text.setAttribute("font-size", String(maxSize));
            }
            if (content.length > 28 && typeof text.getComputedTextLength === "function") {
              try {
                if (text.getComputedTextLength() > available) {
                  text.setAttribute("textLength", String(available));
                  text.setAttribute("lengthAdjust", "spacingAndGlyphs");
                }
              } catch {}
            }
          }
        }
      }
      function fitGeneratedVisual() {
        const root = document.getElementById("root");
        const child = root && root.firstElementChild;
        if (!root || !child) return;
        child.style.transform = "";
        child.style.transformOrigin = "";
        child.style.width = "";
        child.style.height = "";
        const scale = Math.min(
          1,
          root.clientWidth / Math.max(child.scrollWidth, 1),
          root.clientHeight / Math.max(child.scrollHeight, 1),
        );
        if (scale < 0.98) {
          child.style.transformOrigin = "top left";
          child.style.transform = "scale(" + scale + ")";
          child.style.width = 100 / scale + "%";
          child.style.height = 100 / scale + "%";
        }
      }
      try {
        const run = new Function("React", "ReactDOM", "d3", "paperbasePresentation", generatedCode);
        run(window.React, window.ReactDOM, window.d3, window.paperbasePresentation);
        requestAnimationFrame(() => {
          tightenGeneratedVisual();
          fitGeneratedVisual();
        });
        window.addEventListener("resize", () => {
          tightenGeneratedVisual();
          fitGeneratedVisual();
        });
        if (typeof ResizeObserver !== "undefined") {
          new ResizeObserver(notifyPresentationResize).observe(document.getElementById("root"));
        }
      } catch (error) {
        showPresentationError(error);
      }
    </script>
  </body>
</html>`;
}

export function ProjectPresentationCanvas({
  visualCode,
  imageUrl = "",
  title = "Project presentation visual",
}: ProjectPresentationCanvasProps) {
  const srcDoc = useMemo(() => projectPresentationSrcDoc(visualCode), [visualCode]);

  if (imageUrl) {
    return <img className="project-presentation-generated-image" src={imageUrl} alt={title} />;
  }

  if (!visualCode.trim()) {
    return (
      <div className="project-presentation-canvas-empty">
        <span>Visual pending</span>
      </div>
    );
  }

  return (
    <iframe
      className="project-presentation-iframe"
      title={title}
      sandbox="allow-scripts"
      srcDoc={srcDoc}
    />
  );
}
