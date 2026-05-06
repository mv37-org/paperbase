import { describe, expect, it } from "vitest";

import { extractStreamingPythonPreview } from "./CodeSnippet";

describe("extractStreamingPythonPreview", () => {
  it("returns display markdown without the active streaming Python fence", () => {
    const preview = extractStreamingPythonPreview(
      "Here is the script:\n\n```python\ndef train():\n    return 1\n",
    );

    expect(preview).toMatchObject({
      language: "python",
      code: "def train():\n    return 1",
      displayMarkdown: "Here is the script:",
    });
  });

  it("ignores closed and non-Python fences", () => {
    expect(extractStreamingPythonPreview("```python\ndef train():\n    return 1\n```")).toBeNull();
    expect(extractStreamingPythonPreview('```json\n{"status": "ok"}\n')).toBeNull();
  });
});
