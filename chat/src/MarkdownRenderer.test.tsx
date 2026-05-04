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
    expect(screen.getByRole("button", { name: "Collapse code block" })).toBeTruthy();
    expect(document.querySelector(".code-snippet-token.keyword")?.textContent).toBe("def");

    fireEvent.click(screen.getByRole("button", { name: "Collapse code block" }));
    expect(screen.getByRole("button", { name: "Expand code block" })).toBeTruthy();
    expect(screen.queryByText("def")).toBeNull();
  });

  it("keeps longer Python blocks expanded by default", () => {
    const lines = Array.from({ length: 18 }, (_, index) => `print(${index})`).join("\n");

    render(<MarkdownRenderer markdown={`\`\`\`python\n${lines}\n\`\`\``} />);

    expect(screen.getByRole("button", { name: "Collapse code block" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Expand code block" })).toBeNull();
    expect(document.querySelector(".code-snippet-body")?.textContent).toContain("print(17)");
  });

  it("keeps inline code snippets as inline markdown", () => {
    render(<MarkdownRenderer markdown={"Use `torch.nn.Linear` here."} />);

    expect(document.querySelector(".code-snippet-shell")).toBeNull();
    const inlineCode = document.querySelector("code");
    expect(inlineCode?.textContent).toBe("torch.nn.Linear");
  });

  it("renders inline and block LaTeX formulas", () => {
    const { container } = render(
      <MarkdownRenderer markdown={"Inline math $E=mc^2$ and\n\n$$\n\\int_0^1 x^2 \\, dx\n$$"} />,
    );

    expect(container.querySelector(".katex")).toBeTruthy();
    expect(container.querySelector(".katex-display")).toBeTruthy();
    expect(container.textContent).not.toContain("$E=mc^2$");
  });
});
