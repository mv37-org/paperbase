# paper-ai

`paper-ai` has one supported local workflow:

1. copy the repo-root `.env.example` to `.env`
2. run `make setup`
3. run `make doctor`
4. run `make dev`

The repo is intentionally opinionated now:

- one canonical env file at the repo root
- a small repo-root `make` surface, with `make test-smoke` reserved for PR CI
- `vp` is required
- Git hooks run the full verification path before push; PR CI runs a cheaper smoke gate

## Prerequisites

- Python 3.11+
- PostgreSQL with `pgvector` and `pg_trgm` available locally
- [`vp`](https://viteplus.dev/) installed with `curl -fsSL https://vite.plus | bash`

## Env

The only supported local env file is [`.env.example`](./.env.example) copied to `.env` at the repo root.

```bash
cp .env.example .env
```

The sections in that file map to deployment like this:

| Target | Env sections |
| --- | --- |
| `paperbase-api` | `Shared`, `API`, `Cloud / Optional` |
| `paperbase-worker` | `Shared`, `Worker`, `Cloud / Optional` |
| local chat + MCP bridge | `Shared`, `Chat` |

Optional browser-account settings now hang off the same API env surface:

- `PAPERBASE_GOOGLE_*` powers Google sign-in
- `PAPERBASE_GITHUB_*` powers the in-app GitHub connection flow and project repo sync
- logged-in users can store Anthropic keys, Modal sandbox credentials, a signed-in-only light/dark appearance preference, and a weekly papers review email preference

## Commands

`make help` shows the supported local interface:

- `make setup` installs the Python packages, chat dependencies, repo hooks, and a starter `.env`
- `make doctor` verifies `vp`, Python, Postgres, `pgvector`, ports, and the repo env
- `make dev` initializes the local database and storage, then runs the API, worker, MCP bridge, and chat dev server
- `make reset-local` stops the local stack, wipes the local database/storage, and recreates the latest schema
- `make test-smoke` runs the PR-oriented CI smoke gate: lint, chat checks/tests/build, a focused backend subset, and plugin tests
- `make test` runs `ruff check`, `vp check`, a frontend build, backend tests, and plugin tests
- `make profile` runs the deterministic hotpath profiling flow used for performance tracking
- `make clean` stops local services and removes generated logs, caches, and frontend output

## CI

- pull requests run `make test-smoke`
- pushes to `main` and manual verify dispatches run full `make test`
- verify and profile workflows cancel superseded in-progress runs on the same PR or branch
- profiling runs only on `main` pushes or manual dispatch

For a full reset of the local database and storage, use:

```bash
make reset-local
```

The lower-level admin CLI still exposes the same operation as:

```bash
.venv/bin/paperbase-dev flush-local
```

After changing the embedding model in a deployed environment, queue a full
paper/chunk vector rewrite with:

```bash
.venv/bin/paperbase-dev reindex-embeddings
```

Worker job triage goes through the same admin CLI:

```bash
.venv/bin/paperbase-dev jobs list-dead
.venv/bin/paperbase-dev jobs retry <job_id>
```

Historical arXiv backfills are queued with:

```bash
.venv/bin/paperbase-dev harvest --categories cs.LG,cs.CL --start 2015-01-01 --end 2020-12-31 --chunk-months 3 --limit-per-window 1000
```

`--limit-per-window` caps each category-window chunk, not the whole category across the full date range. Harvest chunks run below interactive indexing priority so on-demand `index_paper` jobs can still run during a backfill.

## Local Architecture

The repo has four moving pieces:

- `paperbase/` contains the backend package and both runtime entrypoints
- `paperbase-plugin/` contains the MCP bridge used by Codex and Claude Code
- `chat/` contains the React frontend
- `paperbase/src/paperbase/static/` is generated build output, not source
- the `paperbase-api` root HTML response injects the current browser session into the shell so the frontend can boot without an immediate session fetch and can apply signed-in theme state before React mounts

`paperbase-api` and `paperbase-worker` are the same codebase and image with different entrypoints:

- `paperbase-api` serves the FastAPI application
- `paperbase-worker` runs the background job loop

The browser chat experience is project-based:

- projects are persisted in the backend and own chat sessions
- creating a new project requires a connected GitHub account and either an existing accessible repo/branch selected from the GitHub-backed repo dropdown or a new private repo created under the connected user
- the new-project modal can refresh the accessible repo list, link to GitHub's Paperbase access settings, and expose optional inference/sandbox credential fields
- existing repos expose a branch picker and optional import selection for `.py` and `.ipynb` files; new repos start on a fixed `main` branch
- the sidebar lazily loads mixed project items per project, persists the last selected project across reloads, and reopens with only that project tree expanded by default
- the empty first-chat state now shows a dismissible setup checklist for GitHub, inference, and sandbox access so users can connect required services before the coding agent hits save/run failures
- project descriptions are snapped onto new sessions as per-session prompt context, so later project edits do not rewrite existing chats
- once the first user prompt lands in a `New chat` thread, Paperbase renames the session to a compact 3-5 word summary and keeps that title for later turns
- Python and notebook files are project-scoped tabs backed by Monaco in a high-contrast Python editor view
- project code files can be renamed or deleted directly from the sidebar Code section, projects themselves can be deleted from the project menu including the default `General` project, and if a user has no projects left the app drops into a create-project-only state until a new project is created
- project chats can invoke a project-scoped coding tool surface against repo-relative workspace text files, including list/read/grep/write/patch/move/delete/git-diff/exec/test/lint/run operations instead of raw repo-shell access
- coding turns auto-persist dirty project Python drafts before the agent runs tools, so the backend tool view stays aligned with Monaco edits
- duplicate Python basenames are disambiguated with repo-path context in the sidebar and tab strip
- assistant commentary is now rendered as its own labeled chat item instead of being flattened into the final answer message
- streamed and finished chat code blocks use matching light/dark editor-style themes, show line numbers, let longer snippets collapse in place with a compact chevron control, and render inline/block LaTeX formulas in chat markdown
- project Python files written by coding-tool turns refresh back into the sidebar Code section once the turn completes
- local `localhost` chat threads render a compact debug strip with the current user, project, thread, and latest message ids so Codex can inspect backend state from a pasted bundle without displacing the empty-chat layout
- notebooks are converted with Jupytext to `py:percent` source for editing/running, then regenerated back to `.ipynb` when saved to GitHub
- signed-in pages support a persisted appearance setting, so the sidebar, chat shell, markdown surfaces, drawers, and modals can switch between light and dark mode without affecting the public landing page
- creating, importing, or saving project files requires a connected GitHub account with repo access
- the coding agent is PyTorch-first for generated ML code, uses a Codex-style single turn loop with `tool_choice=auto`, prefers reading and patching existing workspace files before broad rewrites, exposes research tools as optional support rather than the primary posture on project threads, can edit repo-relative text files beyond just managed `.py` sources, keeps turn-local diff and exec-session state visible to the model during long turns and compaction, streams final answers live instead of buffering them until the end, and can run stateful project commands with follow-up wait/log/cancel steps alongside compatibility `project_run` smoke/full entrypoints
- `Save` creates a single Paperbase-managed GitHub commit for the current project, and `Run` saves first, then executes the full saved project workspace through the user’s Modal sandbox credentials with `full` mode by default
- project sync writes only Paperbase-managed paths tracked in `.paperbase`, preserving unrelated files in existing repositories
- if GitHub has moved ahead since the last project save, Paperbase opens a Monaco conflict-resolution flow before creating the next commit

`make dev` runs all of these locally:

- API at `http://127.0.0.1:8080`
- chat dev server at `http://127.0.0.1:5173`
- MCP bridge at `http://127.0.0.1:8090/mcp`

API, worker, and plugin logs go to `.run/`.

## Frontend Output

The frontend bundle served by `paperbase` is built from `chat/` into `paperbase/src/paperbase/static/`.

- that directory is generated and ignored by Git
- `make test` builds it before backend tests because the API serves those files
- `make test-smoke` also builds it so PR CI still catches frontend bundle breakage
- if you need the built shell without the dev server, run `cd chat && vp build`

## Deploy Notes

Cloud Run stays split by service, even though the codebase is shared:

- `paperbase-api` deploys the API entrypoint
- `paperbase-worker` deploys the worker entrypoint

Use the single repo-root env template as the source of truth, then apply the relevant sections to each Cloud Run service instead of maintaining separate tracked env-example files.
