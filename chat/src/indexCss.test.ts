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
    const cardBlock = css.match(/\.trending-card\s*\{([^}]*)\}/)?.[1] ?? "";
    const imageBlock = css.match(/\.trending-card-image\s*\{([^}]*)\}/)?.[1] ?? "";

    expect(cardBlock).toContain("min-height: 282px;");
    expect(cardBlock).toContain('font-family: Georgia, "Times New Roman", serif;');
    expect(cardBlock).toContain("border-radius: 4px;");
    expect(imageBlock).toContain("object-fit: contain;");
    expect(css).toContain(".trending-card-title-button");
  });
});
