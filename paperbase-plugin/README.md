# paperbase-plugin

`paperbase-plugin/` contains the MCP bridge that talks to the `paperbase` backend.

Use the repo-root [README](../README.md) for setup, env, and the supported local workflow.

The MCP surface exposes `paperbase_shell(command: str)` for literature work and keeps operational tools such as `index_paper`, `sync_latest_papers`, `get_job`, and `cancel_job`. Use shell commands like `search "query" -n 20`, `results latest`, `find /papers/{paper_id}/ "*.md"`, `lines /papers/{paper_id}/sections/method.md 40 90`, `grep "pattern" --from latest`, `map --from latest --query "..."`, `sql "select paper_id,title from papers limit 20"`, and `ask-image /papers/{paper_id}/figures/{figure_id}/ "what is shown?"` for caption plus vision-model figure inspection instead of the legacy per-endpoint literature tools.
