# paperbase

`paperbase/` contains the backend package, the API entrypoint, the worker entrypoint, and the `paperbase-dev` local admin CLI.

The admin CLI includes `paperbase-dev reindex-embeddings` for queuing a full
paper/chunk embedding rewrite after an embedding-model change.

The browser backend now persists first-class projects in addition to chat sessions:

- each browser user gets a default `General` project during initialization
- sessions belong to a project, and project session lists are paginated for the sidebar
- project descriptions are copied onto new chat threads as snapped prompt context so existing chats stay stable when a project is edited later

Use the repo-root [README](../README.md) for setup, env, and supported commands.
