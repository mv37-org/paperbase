---
name: map
description: Synthesize answers across multiple papers using paperbase result sets and artifact reads.
---

# Map Workflow

Use this workflow when the user wants an answer across multiple papers rather than a single paper.

1. Use `search_papers` or `get_results` to obtain a `result_set_id`.
2. Read `get_paper`, `get_paper_manifest`, and `read_paper_file` for the most relevant papers.
3. Synthesize the answer directly from extracted artifacts.
4. Prefer sections, tables, and figure captions over abstracts when available.
