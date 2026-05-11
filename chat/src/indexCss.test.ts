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
    const landingTrendingBlock = css.match(/\.landing-page-trending\s*\{([^}]*)\}/)?.[1] ?? "";
    const landingGradientBlock =
      css.match(/\.landing-page-trending::before\s*\{([^}]*)\}/)?.[1] ?? "";
    const gridBlock = css.match(/\.trending-card-grid\s*\{([^}]*)\}/)?.[1] ?? "";
    const publicGridMatch = css.match(
      /\.trending-page-public \.trending-card-grid\s*,\s*\.trending-page-landing \.trending-card-grid\s*\{([^}]*)\}/,
    );
    const publicGridBlock = publicGridMatch?.[1] ?? "";
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
    const publicImageBlock =
      css.match(/\.trending-page-public \.trending-card-image\s*\{([^}]*)\}/)?.[1] ?? "";
    const publicCardBlockMatch = css.match(
      /\.trending-page-public \.trending-card\s*,\s*\.trending-page-landing \.trending-card\s*\{([^}]*)\}/,
    );
    const publicCardBlock = publicCardBlockMatch?.[1] ?? "";
    const publicCardCopyBlockMatch = css.match(
      /\.trending-page-public \.trending-card-copy\s*,\s*\.trending-page-landing \.trending-card-copy\s*\{([^}]*)\}/,
    );
    const publicCardCopyBlock = publicCardCopyBlockMatch?.[1] ?? "";
    const publicCardTitleBlockMatch = css.match(
      /\.trending-page-public \.trending-card-copy h2\s*,\s*\.trending-page-landing \.trending-card-copy h2\s*\{([^}]*)\}/,
    );
    const publicCardTitleBlock = publicCardTitleBlockMatch?.[1] ?? "";
    const publicCardMetaBlockMatch = css.match(
      /\.trending-page-public \.trending-card-copy span\s*,\s*\.trending-page-landing \.trending-card-copy span\s*\{([^}]*)\}/,
    );
    const publicCardMetaBlock = publicCardMetaBlockMatch?.[1] ?? "";
    const detailHeroBlock = css.match(/\.trending-detail-hero\s*\{([^}]*)\}/)?.[1] ?? "";
    const detailImageWrapBlock = css.match(/\.trending-detail-image-wrap\s*\{([^}]*)\}/)?.[1] ?? "";
    const detailImageBlock = css.match(/\.trending-detail-image\s*\{([^}]*)\}/)?.[1] ?? "";
    const detailCopyBlock = css.match(/\.trending-detail-copy\s*\{([^}]*)\}/)?.[1] ?? "";
    const publicDetailTitleBlock =
      css.match(/\.trending-page-public \.workspace-page-header h1\s*\{([^}]*)\}/)?.[1] ?? "";
    const publicDetailDescriptionBlock =
      css.match(/\.trending-page-public \.workspace-page-header p\s*\{([^}]*)\}/)?.[1] ?? "";
    const publicDetailImageWrapBlock =
      css.match(/\.trending-page-public \.trending-detail-image-wrap\s*\{([^}]*)\}/)?.[1] ?? "";
    const publicDetailCopyMatch = css.match(
      /\.trending-page-public \.trending-detail-copy\s*,\s*\.trending-page-public \.trending-comments-section\s*\{([^}]*)\}/,
    );
    const publicDetailCopyBlock = publicDetailCopyMatch?.[1] ?? "";
    const publicDetailActionMatch = css.match(
      /\.trending-page-public \.trending-detail-actions \.trending-card-link\s*,\s*\.trending-page-public \.trending-detail-actions \.trending-vote-button\s*\{([^}]*)\}/,
    );
    const publicDetailActionBlock = publicDetailActionMatch?.[1] ?? "";
    const publicDetailHeadingMatch = css.match(
      /\.trending-page-public \.trending-summary-section h2\s*,\s*\.trending-page-public \.trending-metadata-section h2\s*,\s*\.trending-page-public \.trending-section-heading h2\s*\{([^}]*)\}/,
    );
    const publicDetailHeadingBlock = publicDetailHeadingMatch?.[1] ?? "";
    const publicDetailBodyMatch = css.match(
      /\.trending-page-public \.trending-summary-section p\s*,\s*\.trending-page-public \.trending-section-heading p\s*,\s*\.trending-page-public \.trending-auth-callout p\s*\{([^}]*)\}/,
    );
    const publicDetailBodyBlock = publicDetailBodyMatch?.[1] ?? "";
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
    expect(landingTrendingBlock).toContain("background: #ffffff;");
    expect(css).toContain(".landing-page-trending::before");
    expect(landingGradientBlock).toContain("height: 150px;");
    expect(landingGradientBlock).toContain(
      "background: linear-gradient(to bottom, #f4efe6 0, #ffffff 150px);",
    );
    expect(landingGradientBlock).toContain("opacity: 0.46;");
    expect(landingGradientBlock).not.toContain("radial-gradient");
    expect(gridBlock).toContain("grid-template-columns: repeat(3, minmax(0, 1fr));");
    expect(gridBlock).toContain("gap: 16px;");
    expect(publicGridBlock).toContain("grid-template-columns: repeat(2, minmax(0, 1fr));");
    expect(publicGridBlock).toContain("gap: 22px;");
    expect(landingGridBlock).toContain("grid-template-columns: repeat(2, minmax(0, 1fr));");
    expect(landingGridBlock).toContain("gap: 22px;");
    expect(publicTrendingHeaderBlock).toContain("width: min(100%, 900px);");
    expect(publicTrendingHeaderBlock).toContain('font-family: Georgia, "Iowan Old Style", serif;');
    expect(publicTrendingHeaderBlock).toContain("font-size: 12px;");
    expect(publicTrendingColumnBlock).toContain("max-width: 900px;");
    expect(css).not.toContain(".trending-page-landing .trending-card:nth-child(even)");
    expect(css).not.toContain('"media copy"');
    expect(cardBlock).toContain("min-height: 282px;");
    expect(cardBlock).toContain('font-family: Georgia, "Times New Roman", serif;');
    expect(cardBlock).toContain("border-radius: 4px;");
    expect(imageBlock).toContain("height: clamp(108px, 15vw, 132px);");
    expect(imageBlock).toContain("object-fit: contain;");
    expect(landingImageBlock).toContain("aspect-ratio: 4 / 3;");
    expect(landingImageBlock).toContain("border-radius: 0 0 5px 5px;");
    expect(landingImageBlock).toContain("object-fit: cover;");
    expect(publicImageBlock).toContain("aspect-ratio: 4 / 3;");
    expect(publicImageBlock).toContain("border-radius: 0 0 5px 5px;");
    expect(publicImageBlock).toContain("object-fit: cover;");
    expect(publicCardBlock).toContain("grid-template-rows: auto minmax(0, 1fr) auto;");
    expect(publicCardBlock).toContain("gap: 0;");
    expect(publicCardBlock).toContain("padding: 0;");
    expect(publicCardCopyBlock).toContain("padding: 8px 9px 7px;");
    expect(publicCardTitleBlock).toContain("font-size: clamp(11px, 1.25vw, 14px);");
    expect(publicCardTitleBlock).toContain("font-weight: 650;");
    expect(publicCardMetaBlock).toContain("font-size: 11px;");
    expect(detailHeroBlock).toContain("grid-template-columns: 1fr;");
    expect(detailImageWrapBlock).toContain("place-items: center;");
    expect(detailImageWrapBlock).toContain("width: min(100%, 1040px);");
    expect(detailImageWrapBlock).toContain("padding: 0;");
    expect(publicDetailTitleBlock).toContain('font-family: Georgia, "Iowan Old Style", serif;');
    expect(publicDetailTitleBlock).toContain("font-size: clamp(25px, 3vw, 36px);");
    expect(publicDetailTitleBlock).toContain("font-weight: 500;");
    expect(publicDetailDescriptionBlock).toContain("font-size: 14px;");
    expect(publicDetailImageWrapBlock).toContain("width: 100%;");
    expect(publicDetailImageWrapBlock).toContain("border-radius: 10px;");
    expect(detailImageBlock).toContain("height: auto;");
    expect(detailImageBlock).toContain("border: 0;");
    expect(detailImageBlock).toContain("object-fit: contain;");
    expect(detailImageBlock).not.toContain("max-height:");
    expect(detailImageBlock).not.toContain("aspect-ratio:");
    expect(detailCopyBlock).toContain("width: min(100%, 980px);");
    expect(publicDetailCopyBlock).toContain("max-width: 900px;");
    expect(publicDetailActionBlock).toContain("font-size: 12px;");
    expect(publicDetailHeadingBlock).toContain("font-size: 18px;");
    expect(publicDetailHeadingBlock).toContain("font-weight: 500;");
    expect(publicDetailBodyBlock).toContain("font-size: 15px;");
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
