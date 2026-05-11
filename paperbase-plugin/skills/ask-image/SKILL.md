---
name: ask-image
description: Inspect paper figure metadata and captions through the Paperbase literature shell.
---

# Ask-Image Workflow

Use this workflow when the user asks about a paper image, chart, architecture diagram, or visual result.

1. Use `paperbase_shell` with `cat /papers/{paper_id}/manifest.json` to see whether figures are present.
2. Use `paperbase_shell` with `ls /papers/{paper_id}/figures`, `find /papers/{paper_id}/figures`, and `ask-image /papers/{paper_id}/figures/{figure_id}/ "question"` to fetch figure metadata, captions, and run a vision-model inspection when an inference key is configured.
3. If the command reports that image pixels were unavailable or no inference key is configured, answer from caption evidence and say when the image pixels were not inspected.
4. Ground the answer in the figure path and extracted caption.
