---
name: ask-image
description: Inspect paper figure metadata and captions through the Paperbase literature shell.
---

# Ask-Image Workflow

Use this workflow when the user asks about a paper image, chart, architecture diagram, or visual result.

1. Use `paperbase_shell` with `cat /papers/{paper_id}/manifest.json` to see whether figures are present.
2. Use `paperbase_shell` with `ls /papers/{paper_id}/figures` and `cat /papers/{paper_id}/figures/{figure_id}/caption.md` to fetch figure metadata and captions.
3. If only captions are available through the shell, answer from caption evidence and say when the image itself was not inspected.
4. Ground the answer in the figure path and extracted caption.
