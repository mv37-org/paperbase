import { fireEvent, render, screen } from "@testing-library/react";
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

  it("renders collapsible editor-styled Python blocks with line numbers", () => {
    const markdown = "```python\ndef train():\n    return 1\n```";

    render(<MarkdownRenderer markdown={markdown} />);

    expect(screen.getByText("python")).toBeTruthy();
    expect(screen.getByText("2 lines")).toBeTruthy();
    const lineNumbers = [...document.querySelectorAll(".code-snippet-line-number")].map(
      (element) => element.textContent,
    );
    expect(lineNumbers).toEqual(["1", "2"]);
    expect(screen.getByText("Hide code")).toBeTruthy();
    expect(document.querySelector(".code-snippet-token.keyword")?.textContent).toBe("def");

    fireEvent.click(screen.getByRole("button", { name: "Hide code" }));
    expect(screen.getByRole("button", { name: "Show code" })).toBeTruthy();
    expect(screen.queryByText("def")).toBeNull();
  });
});
