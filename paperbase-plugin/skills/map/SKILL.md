---
name: map
description: Synthesize answers across multiple papers using paperbase result sets and artifact reads.
---

# Map Workflow

Use this workflow when the user wants an answer across multiple papers rather than a single paper.

1. Use `paperbase_shell` with `search "query" -n 20` or `results rs_x` to obtain a `result_set_id`.
2. Narrow with `grep "pattern" --from rs_x` when a phrase or method should be present.
3. Inspect exact artifacts with `ls`, `head`, and `cat` under `/papers/{paper_id}/`.
4. Use `map --from rs_x --query "..."` for repeated extraction across many papers.
5. Prefer sections, tables, and figure captions over abstracts when available.
