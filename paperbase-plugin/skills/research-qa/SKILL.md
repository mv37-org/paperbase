---
name: research-qa
description: Answer broad AI research questions from indexed papers by using paperbase_shell to search, narrow, inspect, and map grounded evidence.
---

# Research-QA Workflow

Use this workflow when the user asks an open-ended research question such as how to build an agent, what the latest methods are, or how approaches compare across papers.

1. Call `paperbase_shell` with `search "query" -n 20` first and keep the returned `result_set_id`.
2. Use `grep "pattern" --from rs_x` to narrow the set when the question depends on a method, benchmark, or term.
3. Use `ls`, `head`, and `cat` on `/papers/{paper_id}/...` paths to read exact section, table, figure-caption, reference, and metadata artifacts.
4. Use `map --from rs_x --query "..."` when the same extraction should run across many papers.
5. Write the answer from indexed evidence, not from base-model memory, and cite paper paths when making specific claims.
6. If the corpus is thin or the evidence is weak, say that clearly and suggest indexing more papers or widening the query.
