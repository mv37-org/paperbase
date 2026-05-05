# paperbase

`paperbase/` contains the backend package, the API entrypoint, the worker entrypoint, and the `paperbase-dev` local admin CLI.

The admin CLI includes:

- `paperbase-dev reindex-embeddings` for queuing a full paper/chunk embedding rewrite after an embedding-model change
- `paperbase-dev jobs list-dead` for dead-letter triage
- `paperbase-dev jobs retry <job_id>` for promoting a dead job back to pending
- `paperbase-dev jobs cancel <job_id>` for canceling pending jobs or requesting cooperative cancellation for running jobs
- `paperbase-dev harvest --categories cs.LG,cs.CL --start YYYY-MM-DD --end YYYY-MM-DD` for historical arXiv backfills

Job API responses expose `effective_status` in addition to raw `status`: `running` requires an unexpired worker lease, `stalled` means that lease expired, and `canceling` means a running worker has been asked to stop.

For harvests, `--limit-per-window` is a per category-window cap, not a whole-category cap across the full date range. Harvest chunks run at backfill priority below interactive indexing.

The browser backend now persists first-class projects in addition to chat sessions:

- each browser user gets a default `General` project during initialization
- user-created projects must be backed by a connected GitHub repository; the backend can create a private repo on `main` or validate an existing repo and selected branch
- sessions belong to a project, and project session lists are paginated for the sidebar
- project descriptions are copied onto new chat threads as snapped prompt context so existing chats stay stable when a project is edited later
- project files store their repo path and kind (`python` or `notebook`), with notebooks converted through Jupytext for Python-source editing and regenerated to `.ipynb` during repo sync
- repo sync overlays only paths tracked in `.paperbase`, so unrelated existing-repo content is preserved

Use the repo-root [README](../README.md) for setup, env, and supported commands.
