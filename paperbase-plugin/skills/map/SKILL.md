---
name: map
description: Synthesize answers across multiple papers using paperbase result sets and artifact reads.
---

# Map Workflow

Use this workflow when the user wants an answer across multiple papers rather than a single paper.

1. Use `paperbase_shell` with `search "query" -n 20`, `results latest`, or `sql "select paper_id,title from papers limit 20"` to obtain a `result_set_id`.
2. Narrow with `grep "pattern" --from latest` when a phrase or method should be present.
3. Inspect exact artifacts with `ls`, `find`, `head`, `lines`, and `cat` under `/papers/{paper_id}/`.
4. Use `map --from latest --query "..."` for repeated extraction across many papers.
5. Prefer sections, tables, and figure captions over abstracts when available.
