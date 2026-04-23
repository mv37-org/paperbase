import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MarkdownRenderer } from "./MarkdownRenderer";

describe("MarkdownRenderer", () => {
  it("repairs legacy flattened tables before rendering", () => {
    const markdown = `Here are the most recently indexed papers in the library, ordered by when they were added: | Paper | arXiv ID | Indexed At |
|---|---|---|
| [Generalization at the Edge of Stability](https://arxiv.org/abs/2604.19740) | \`2604.19740\` | *currently indexing* |
| [The BrowserGym Ecosystem for Web Agent Research](https://arxiv.org/abs/2412.05467) | \`2412.05467\` | 2026-04-19 | The most recent by publication date is **Generalization at the Edge of Stability**.`;

    const { container } = render(<MarkdownRenderer markdown={markdown} />);

    const table = container.querySelector("table");
    expect(table).toBeTruthy();
    expect(table?.querySelectorAll("tbody tr")).toHaveLength(2);
    expect(container.textContent).toContain(
      "Here are the most recently indexed papers in the library, ordered by when they were added:",
    );
    expect(container.textContent).toContain(
      "The most recent by publication date is Generalization at the Edge of Stability.",
    );
  });
});
