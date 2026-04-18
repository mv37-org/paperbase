---
name: ask-image
description: Inspect paper figures through paperbase figure metadata and image URLs.
---

# Ask-Image Workflow

Use this workflow when the user asks about a paper image, chart, architecture diagram, or visual result.

1. Retrieve `get_paper_manifest` to see whether figures are present.
2. Use `get_figure` to fetch the figure caption and image URL.
3. Use the host model's multimodal capabilities on the returned image URL.
4. Ground the answer in both the figure image and the extracted caption.
