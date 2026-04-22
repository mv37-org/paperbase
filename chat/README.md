# chat

`chat/` contains the React frontend for the browser experience.

Source lives here. Generated assets are built into `paperbase/src/paperbase/static/` and are not tracked.

The landing prompt and authenticated chat composer both support live `@` paper mentions backed by the browser suggestion endpoint, so tagged-paper search suggestions come from the indexed paper corpus instead of local mock data.

The authenticated sidebar is project-first:

- projects expand and collapse independently and lazily fetch mixed project items
- each project can start new chats, be renamed, or be deleted from the sidebar
- chat rows expose delete-on-hover actions and only fetch full thread content when opened
- Python file rows open Monaco-backed editor tabs with explicit `Save` and `Run` actions
- Python file creation and save flows require a connected GitHub account, and `Run` saves to GitHub before executing through Daytona
- project sync writes `/src`, `/notes`, and `.paperbase`, and GitHub conflicts reopen inside a Monaco diff-and-merge modal
- project descriptions act as instructions for new sessions by being snapshotted onto the thread at creation time

Use the repo-root [README](../README.md) for setup, env, and the supported local workflow.
