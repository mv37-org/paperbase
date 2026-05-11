# paperbase-plugin

`paperbase-plugin/` contains the MCP bridge that talks to the `paperbase` backend.

Use the repo-root [README](../README.md) for setup, env, and the supported local workflow.

The MCP surface exposes `paperbase_shell(command: str)` for literature work and keeps operational tools such as `index_paper`, `sync_latest_papers`, `get_job`, and `cancel_job`. Use shell commands like `search "query" -n 20`, `ls /papers/{paper_id}/`, `head -n 80 /papers/{paper_id}/sections/method.lines`, `grep "pattern" --from rs_x`, and `map --from rs_x --query "..."` instead of the legacy per-endpoint literature tools.
