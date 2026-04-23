# paperbase

`paperbase/` contains the backend package, the API entrypoint, the worker entrypoint, and the `paperbase-dev` local admin CLI.

The admin CLI includes:

- `paperbase-dev reindex-embeddings` for queuing a full paper/chunk embedding rewrite after an embedding-model change
- `paperbase-dev jobs list-dead` for dead-letter triage
- `paperbase-dev jobs retry <job_id>` for promoting a dead job back to pending
- `paperbase-dev harvest --categories cs.LG,cs.CL --start YYYY-MM-DD --end YYYY-MM-DD` for historical arXiv backfills

For harvests, `--limit-per-window` is a per category-window cap, not a whole-category cap across the full date range. Harvest chunks run at backfill priority below interactive indexing.

The browser backend now persists first-class projects in addition to chat sessions:

- each browser user gets a default `General` project during initialization
- sessions belong to a project, and project session lists are paginated for the sidebar
- project descriptions are copied onto new chat threads as snapped prompt context so existing chats stay stable when a project is edited later

Use the repo-root [README](../README.md) for setup, env, and supported commands.
