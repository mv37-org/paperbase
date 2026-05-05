# chat

`chat/` contains the React frontend for the browser experience.

Source lives here. Generated assets are built into `paperbase/src/paperbase/static/` and are not tracked.

The server-rendered HTML shell now inlines the current browser session and applies the authenticated dark-theme class before React mounts, so reloads do not block on a second `/v1/browser/session` round trip or flash a white shell for signed-in dark-theme users.

The landing prompt and authenticated chat composer both support live `@` paper mentions backed by the browser suggestion endpoint, so tagged-paper search suggestions come from the indexed paper corpus instead of local mock data.

The authenticated sidebar is project-first:

- projects expand and collapse independently and lazily fetch mixed project items
- each project can start new chats, be renamed, or be deleted from the sidebar, but new projects require a connected GitHub repo
- the new-project modal lists accessible GitHub repos in a custom dropdown, with "Create new repo" as the first option and `main` fixed for newly created repos
- existing repo selection includes a branch picker, repo/branch refresh, a GitHub access-management link, and optional `.py`/`.ipynb` imports
- the modal's advanced section exposes inference and Modal sandbox credential entry points for users who have not configured them yet
- the selected project persists across reloads, and the sidebar reopens with that project selected and its tree as the only expanded root by default
- empty first-chat screens include a dismissible setup checklist that points users to GitHub, inference, and sandbox settings before the coding agent starts doing repo-backed work
- chat rows expose delete-on-hover actions and only fetch full thread content when opened
- `New chat` sessions are renamed after the first real user prompt to a compact 3-5 word summary and then keep that title on later turns
- chat markdown renders inline/block LaTeX formulas, while localhost-only thread debug metadata stays out of the empty-chat landing layout
- assistant commentary and final answers render as separate persisted chat items, so progress notes are no longer visually merged into the final response
- signed-in pages can switch between light and dark mode from Settings → Appearance, and the theme covers the shell, markdown, drawers, icons, and modals without changing the public landing page
- Python and imported notebook rows open Monaco-backed Python editor tabs with explicit `Save` and `Run` actions
- project coding turns use the Codex-style turn loop and can mix commentary, tool calls, and final answers in a single persisted turn while grounding AI/code implementation choices in relevant paper or uploaded-document evidence when available
- `.ipynb` and Colab uploads are converted through Jupytext to `py:percent` source for editing, while GitHub saves regenerate the notebook path
- project file creation/import/save flows require a connected GitHub account, and `Run` saves to GitHub before executing through Modal
- project sync writes only `.paperbase` managed paths, and GitHub conflicts reopen inside a Monaco diff-and-merge modal
- project descriptions act as instructions for new sessions by being snapshotted onto the thread at creation time

Use the repo-root [README](../README.md) for setup, env, and the supported local workflow.
