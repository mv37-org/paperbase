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
    const workspaceTrendingBlock = css.match(/\.workspace-page-trending\s*\{([^}]*)\}/)?.[1] ?? "";
    const publicTrendingHeaderBlock =
      css.match(/\.public-trending-header\s*\{([^}]*)\}/)?.[1] ?? "";
    const publicTrendingColumnBlock =
      css.match(/\.public-trending-column\s*\{([^}]*)\}/)?.[1] ?? "";
    const cardBlock = css.match(/\.trending-card\s*\{([^}]*)\}/)?.[1] ?? "";
    const imageBlock = css.match(/\.trending-card-image\s*\{([^}]*)\}/)?.[1] ?? "";
    const landingImageBlock =
      css.match(/\.trending-page-landing \.trending-card-image\s*\{([^}]*)\}/)?.[1] ?? "";
    const detailHeroBlock = css.match(/\.trending-detail-hero\s*\{([^}]*)\}/)?.[1] ?? "";
    const detailImageWrapBlock = css.match(/\.trending-detail-image-wrap\s*\{([^}]*)\}/)?.[1] ?? "";
    const detailImageBlock = css.match(/\.trending-detail-image\s*\{([^}]*)\}/)?.[1] ?? "";
    const detailCopyBlock = css.match(/\.trending-detail-copy\s*\{([^}]*)\}/)?.[1] ?? "";
    const metadataGridBlock = css.match(/\.trending-metadata-grid\s*\{([^}]*)\}/)?.[1] ?? "";
    const metadataRowBlock = css.match(/\.trending-metadata-row\s*\{([^}]*)\}/)?.[1] ?? "";
    const landingDetailTitleMatch = css.match(
      /\.trending-page-landing\.detail-open \.workspace-page-header h2\s*\{([^}]*)\}/,
    );
    const landingDetailTitleBlock = landingDetailTitleMatch?.[1] ?? "";
    const activeVoteBlock = css.match(/\.trending-vote-button\.active\s*\{([^}]*)\}/)?.[1] ?? "";

    expect(workspaceTrendingBlock).toContain(
      "width: min(var(--chat-content-max-width), calc(100% - 48px));",
    );
    expect(gridBlock).toContain("grid-template-columns: repeat(3, minmax(0, 1fr));");
    expect(gridBlock).toContain("gap: 16px;");
    expect(landingGridBlock).toContain("grid-template-columns: repeat(2, minmax(0, 1fr));");
    expect(landingGridBlock).toContain("gap: 16px;");
    expect(publicTrendingHeaderBlock).toContain("width: min(100%, 900px);");
    expect(publicTrendingColumnBlock).toContain("max-width: 900px;");
    expect(css).not.toContain(".trending-page-landing .trending-card:nth-child(even)");
    expect(css).not.toContain('"media copy"');
    expect(cardBlock).toContain("min-height: 282px;");
    expect(cardBlock).toContain('font-family: Georgia, "Times New Roman", serif;');
    expect(cardBlock).toContain("border-radius: 4px;");
    expect(imageBlock).toContain("height: clamp(108px, 15vw, 132px);");
    expect(imageBlock).toContain("object-fit: contain;");
    expect(landingImageBlock).toContain("height: clamp(260px, 31vw, 390px);");
    expect(detailHeroBlock).toContain("grid-template-columns: 1fr;");
    expect(detailImageWrapBlock).toContain("place-items: center;");
    expect(detailImageWrapBlock).toContain("width: min(100%, 1040px);");
    expect(detailImageWrapBlock).toContain("padding: 0;");
    expect(detailImageBlock).toContain("height: auto;");
    expect(detailImageBlock).toContain("border: 0;");
    expect(detailImageBlock).toContain("object-fit: contain;");
    expect(detailImageBlock).not.toContain("max-height:");
    expect(detailImageBlock).not.toContain("aspect-ratio:");
    expect(detailCopyBlock).toContain("width: min(100%, 980px);");
    expect(metadataGridBlock).toContain("display: grid;");
    expect(metadataRowBlock).toContain(
      "grid-template-columns: minmax(116px, 160px) minmax(0, 1fr);",
    );
    expect(metadataRowBlock).toContain("padding: 6px 0;");
    expect(landingDetailTitleBlock).toContain("font-size: clamp(22px, 3vw, 34px);");
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
