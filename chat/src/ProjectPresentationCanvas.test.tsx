import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ProjectPresentationCanvas } from "./ProjectPresentationCanvas";

describe("ProjectPresentationCanvas", () => {
  it("provides generated visuals with responsive resize helpers", () => {
    render(
      <ProjectPresentationCanvas visualCode="document.getElementById('root').textContent = 'ok';" />,
    );

    const iframe = screen.getByTitle("Project presentation visual") as HTMLIFrameElement;
    const srcDoc = iframe.getAttribute("srcdoc") ?? "";

    expect(srcDoc).toContain("paperbasePresentation");
    expect(srcDoc).toContain("onResize(listener)");
    expect(srcDoc).toContain("presentationSize()");
    expect(srcDoc).toContain("new ResizeObserver(notifyPresentationResize)");
    expect(srcDoc).toContain("paperbase:presentation-resize");
    expect(srcDoc).toContain('new Function("React", "ReactDOM", "d3", "paperbasePresentation"');
  });
});
