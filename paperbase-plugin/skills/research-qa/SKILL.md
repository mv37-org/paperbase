---
name: research-qa
description: Answer broad AI research questions from indexed papers by using paperbase_shell to search, narrow, inspect, and map grounded evidence.
---

# Research-QA Workflow

Use this workflow when the user asks an open-ended research question such as how to build an agent, what the latest methods are, or how approaches compare across papers.

1. Call `paperbase_shell` with `search "query" -n 20` first and keep the returned `result_set_id`.
2. Use `results latest` and `results prev` to keep track of the current and previous result sets during a long traversal.
3. Use `grep "pattern" --from latest` to narrow the set when the question depends on a method, benchmark, or term.
4. Use `ls`, `find`, `head`, `lines`, and `cat` on `/papers/{paper_id}/...` paths to read exact section, table, figure-caption, reference, and metadata artifacts.
5. Use `sql "select paper_id,title,published from papers limit 20"` for corpus inventory and `ask-image /papers/{paper_id}/figures/{figure_id}/ "question"` for figure caption and image URL access.
6. Use `map --from latest --query "..."` when the same extraction should run across many papers.
7. Write the answer from indexed evidence, not from base-model memory, and cite paper paths when making specific claims.
8. If the corpus is thin or the evidence is weak, say that clearly and suggest indexing more papers or widening the query.
