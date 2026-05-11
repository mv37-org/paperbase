import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

const css = readFileSync("src/index.css", "utf8");

describe("index.css", () => {
  it("keeps the authenticated app shell out of document flow", () => {
    const block = css.match(/\.app-shell\s*\{([^}]*)\}/)?.[1] ?? "";

    expect(block).toContain("position: fixed;");
    expect(block).toContain("inset: 0;");
    expect(block).toContain("overflow: hidden;");
    expect(css).toContain("body:has(.app-shell)");
  });

  it("keeps paper exploration outer scroll containers clipped in all layout rules", () => {
    const blocks = Array.from(
      css.matchAll(/\.chat-message-list\.paper-exploration-view\s*\{([^}]*)\}/g),
      (match) => match[1] ?? "",
    );

    expect(blocks.length).toBeGreaterThanOrEqual(2);
    expect(blocks.every((block) => /overflow:\s*hidden;/.test(block))).toBe(true);
  });

  it("keeps trending paper cards aligned with research paper card styling", () => {
    const gridBlock = css.match(/\.trending-card-grid\s*\{([^}]*)\}/)?.[1] ?? "";
    const landingGridBlock =
      css.match(/\.trending-page-landing \.trending-card-grid\s*\{([^}]*)\}/)?.[1] ?? "";
    const landingCardBlock =
      css.match(/\.trending-page-landing \.trending-card\s*\{([^}]*)\}/)?.[1] ?? "";
    const landingEvenCardBlock =
      css.match(/\.trending-page-landing \.trending-card:nth-child\(even\)\s*\{([^}]*)\}/)?.[1] ??
      "";
    const workspaceTrendingBlock = css.match(/\.workspace-page-trending\s*\{([^}]*)\}/)?.[1] ?? "";
    const cardBlock = css.match(/\.trending-card\s*\{([^}]*)\}/)?.[1] ?? "";
    const imageBlock = css.match(/\.trending-card-image\s*\{([^}]*)\}/)?.[1] ?? "";
    const activeVoteBlock = css.match(/\.trending-vote-button\.active\s*\{([^}]*)\}/)?.[1] ?? "";

    expect(workspaceTrendingBlock).toContain(
      "width: min(var(--chat-content-max-width), calc(100% - 48px));",
    );
    expect(gridBlock).toContain("grid-template-columns: repeat(3, minmax(0, 1fr));");
    expect(gridBlock).toContain("gap: 16px;");
    expect(landingGridBlock).toContain("grid-template-columns: 1fr;");
    expect(landingGridBlock).toContain("gap: 24px;");
    expect(landingCardBlock).toContain('"media copy"');
    expect(landingCardBlock).not.toContain("actions");
    expect(landingEvenCardBlock).toContain('"media copy"');
    expect(landingEvenCardBlock).not.toContain('"copy media"');
    expect(cardBlock).toContain("min-height: 282px;");
    expect(cardBlock).toContain('font-family: Georgia, "Times New Roman", serif;');
    expect(cardBlock).toContain("border-radius: 4px;");
    expect(imageBlock).toContain("height: clamp(108px, 15vw, 132px);");
    expect(imageBlock).toContain("object-fit: contain;");
    expect(activeVoteBlock).toContain("border-color: #99582a;");
    expect(activeVoteBlock).toContain("color: #99582a;");
    expect(css).toContain(".trending-card-title-button");
    expect(css).toContain(".trending-vote-chevron");
  });

  it("keeps project presentation chrome compact and frameless", () => {
    const canvasBlock = css.match(/\.project-presentation-slide-canvas\s*\{([^}]*)\}/)?.[1] ?? "";
    const headerBlock = css.match(/\.project-presentation-fixed-header\s*\{([^}]*)\}/)?.[1] ?? "";
    const titleBlock = css.match(/\.project-presentation-slide-copy h1\s*\{([^}]*)\}/)?.[1] ?? "";
    const copyBlock = css.match(/\.project-presentation-slide-copy p\s*\{([^}]*)\}/)?.[1] ?? "";
    const imageBlock = css.match(/\.project-presentation-generated-image\s*\{([^}]*)\}/)?.[1] ?? "";
    const audioBlock =
      css.match(/\.project-presentation-audio-player audio\s*\{([^}]*)\}/)?.[1] ?? "";

    expect(canvasBlock).toContain("border: 0;");
    expect(canvasBlock).toContain("background: transparent;");
    expect(headerBlock).toContain("display: flex;");
    expect(headerBlock).toContain("padding: 6px 8px;");
    expect(titleBlock).toContain("font-size: clamp(18px, 1.85vw, 25px);");
    expect(copyBlock).toContain("font-size: clamp(13px, 0.9vw, 15px);");
    expect(imageBlock).toContain("object-fit: contain;");
    expect(audioBlock).toContain("height: 28px;");
    expect(css).toContain("body.authenticated-theme-dark .project-presentation-slide-canvas");
  });
});
