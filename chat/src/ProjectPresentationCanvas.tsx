import { useMemo } from "react";

type ProjectPresentationCanvasProps = {
  visualCode: string;
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
      body { background: #ffffff; color: #1b1611; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
      #root { container-type: size; position: relative; }
      #root > * { max-width: 100%; max-height: 100%; }
      svg, canvas { display: block; max-width: 100%; max-height: 100%; }
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
        const run = new Function("React", "ReactDOM", "d3", generatedCode);
        run(window.React, window.ReactDOM, window.d3);
        requestAnimationFrame(fitGeneratedVisual);
        window.addEventListener("resize", fitGeneratedVisual);
      } catch (error) {
        showPresentationError(error);
      }
    </script>
  </body>
</html>`;
}

export function ProjectPresentationCanvas({
  visualCode,
  title = "Project presentation visual",
}: ProjectPresentationCanvasProps) {
  const srcDoc = useMemo(() => projectPresentationSrcDoc(visualCode), [visualCode]);

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
