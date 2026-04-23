# chat

`chat/` contains the React frontend for the browser experience.

Source lives here. Generated assets are built into `paperbase/src/paperbase/static/` and are not tracked.

The landing prompt and authenticated chat composer both support live `@` paper mentions backed by the browser suggestion endpoint, so tagged-paper search suggestions come from the indexed paper corpus instead of local mock data.

The authenticated sidebar is project-first:

- projects expand and collapse independently and lazily fetch mixed project items
- each project can start new chats, be renamed, or be deleted from the sidebar, but new projects require a connected GitHub repo
- the new-project modal can create a private repo on `main` or link an existing repo after the user picks a base branch and optional `.py`/`.ipynb` imports
- chat rows expose delete-on-hover actions and only fetch full thread content when opened
- signed-in pages can switch between light and dark mode from Settings → Appearance, and the theme covers the shell, markdown, drawers, icons, and modals without changing the public landing page
- Python and imported notebook rows open Monaco-backed Python editor tabs with explicit `Save` and `Run` actions
- `.ipynb` and Colab uploads are converted through Jupytext to `py:percent` source for editing, while GitHub saves regenerate the notebook path
- project file creation/import/save flows require a connected GitHub account, and `Run` saves to GitHub before executing through Modal
- project sync writes only `.paperbase` managed paths, and GitHub conflicts reopen inside a Monaco diff-and-merge modal
- project descriptions act as instructions for new sessions by being snapshotted onto the thread at creation time

Use the repo-root [README](../README.md) for setup, env, and the supported local workflow.
