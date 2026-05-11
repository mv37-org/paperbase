---
name: index-paper
description: Index a paper and report the result in a concise research-friendly summary instead of dumping raw manifests or file paths.
---

# Index-Paper Workflow

Use this workflow when the user asks to index a paper or check whether indexing finished.

1. Call `index_paper` to enqueue the job.
2. Poll `get_job` until the job reaches a terminal state.
3. Once complete, use `paperbase_shell` with `cat /papers/{paper_id}/meta.json` for metadata when needed.
4. Use `paperbase_shell` with `cat /papers/{paper_id}/manifest.json` only to extract high-level facts such as source type and section, figure, or table counts.
5. Use `paperbase_shell` with `ls /papers/{paper_id}/` only when the user explicitly asks for files, paths, or the storage layout.
6. Do not paste raw manifest JSON unless the user explicitly asks for it.
7. Default response shape:
   - one short sentence confirming indexing completed
   - one short paragraph summarizing the paper and extraction quality
   - one short sentence suggesting the next useful action, such as summarizing the paper, opening figures, or comparing it to related work
