# chat

`chat/` contains the React frontend for the browser experience.

Source lives here. Generated assets are built into `paperbase/src/paperbase/static/` and are not tracked.

The server-rendered HTML shell now inlines the current browser session and applies the authenticated dark-theme class before React mounts, so reloads do not block on a second `/v1/browser/session` round trip or flash a white shell for signed-in dark-theme users.

The auth modal presents "Continue with MV37" as the only product sign-in path. Google, Apple, username/password, and future credential methods belong behind MV37 auth, not inside Paperbase.

The landing prompt and authenticated chat composer both support live `@` paper mentions backed by the browser suggestion endpoint, and the public landing page shows a two-column top-10 `Latest`/`Hottest` paper preview from the indexed corpus with clicks routed to the narrower public trending detail page.

The authenticated sidebar is project-first, with a small signed-in Browse area above projects:

- `Trending papers` now works for public read and signed-in interaction, with paginated 20-paper `Latest` and `Hottest` feeds over fully indexed and enriched papers, full-aspect paper previews, curated compact metadata with readable dates and human arXiv categories, exploration-first reading, and nested comments/replies
- allowlisted admins also get an `Admin panel` jobs workspace in that Browse section
- the admin jobs header includes paper search by paper id, arXiv id, title, or author, and the paper detail modal shows summaries, metadata, code state, stored artifacts, and every tracked job touch
- the admin tracked-papers drilldown fetches paginated paper pages per job run, and expanded job rows expose payloads, Cloud Run log filters, and stored failure traces for debugging
- projects expand and collapse independently and lazily fetch mixed project items
- each project can start new chats, be renamed, or be deleted from the sidebar; new projects are local by default and GitHub sync is optional
- each project can also start paper explorations from a command-palette style search over paper title or arXiv id
- each project home can generate a cached presentation with a React/D3 visual, Markdown summary, optional public share page, and optional ElevenLabs two-person audio walkthrough when a key is saved in Settings -> Audio
- paper PDF views expose text-highlight actions and a pen tool for freeform region selection
- when GitHub sync is enabled in Settings, the new-project modal lists accessible GitHub repos in a custom dropdown, with "Create new repo" as the first option and `main` fixed for newly created repos
- synced existing repo selection includes a branch picker, repo/branch refresh, a GitHub access-management link, and optional `.py`/`.ipynb` imports
- the modal's advanced section exposes inference and Modal sandbox credential entry points for users who have not configured them yet
- the selected project persists across reloads, and the sidebar reopens with that project selected and its tree as the only expanded root by default
- empty first-chat screens include a dismissible setup checklist that points users to optional GitHub sync, inference, and sandbox settings
- chat rows expose delete-on-hover actions and only fetch full thread content when opened
- `New chat` sessions are renamed after the first real user prompt to a compact 3-5 word summary and then keep that title on later turns
- chat markdown renders inline/block LaTeX formulas, while localhost-only thread debug metadata stays out of the empty-chat landing layout
- assistant commentary is prompted before non-trivial tool batches, then collapses behind a `Worked for ...` summary once the final answer arrives so session actions stay grouped with the conclusion
- the fixed Conversation/Research papers thread header is hidden until an assistant reply exists, and follow-up prompts anchor the new turn at the top of the chat viewport so prior turns move out of focus
- signed-in pages can switch between light and dark mode from Settings → Appearance, and the theme covers the shell, markdown, drawers, icons, and modals without changing the public landing page
- Python and imported notebook rows open as the single active Monaco-backed editor view with explicit `Save` and `Run` actions
- project coding turns use the Codex-style turn loop and can mix commentary, tool calls, and final answers in a single persisted turn while keeping final answers brief and grounded in cited research-paper evidence for in-scope turns; uploaded-document evidence is used only when explicitly requested
- `.ipynb` and Colab uploads are converted through Jupytext to `py:percent` source for editing, while GitHub saves regenerate the notebook path
- project file creation/import/save flows work locally without a GitHub account; when GitHub sync is enabled, saves can also commit managed files to GitHub
- project sync writes only `.paperbase` managed paths, and GitHub conflicts reopen inside a Monaco diff-and-merge modal
- project descriptions act as instructions for new sessions by being snapshotted onto the thread at creation time

Use the repo-root [README](../README.md) for setup, env, and the supported local workflow.
