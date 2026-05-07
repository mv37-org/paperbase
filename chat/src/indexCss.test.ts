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
});
