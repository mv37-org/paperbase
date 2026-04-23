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
- logged-in users can store Anthropic keys, Modal sandbox credentials, and a weekly papers review email preference

## Commands

`make help` shows the supported local interface:

- `make setup` installs the Python packages, chat dependencies, repo hooks, and a starter `.env`
- `make doctor` verifies `vp`, Python, Postgres, `pgvector`, ports, and the repo env
- `make dev` initializes the local database and storage, then runs the API, worker, MCP bridge, and chat dev server
- `make test-smoke` runs the PR-oriented CI smoke gate: lint, chat checks/tests/build, a focused backend subset, and plugin tests
- `make test` runs `ruff check`, `vp check`, a frontend build, backend tests, and plugin tests
- `make profile` runs the deterministic hotpath profiling flow used for performance tracking
- `make clean` stops local services and removes generated logs, caches, and frontend output

## CI

- pull requests run `make test-smoke`
- pushes to `main` and manual verify dispatches run full `make test`
- verify and profile workflows cancel superseded in-progress runs on the same PR or branch
- profiling runs only on `main` pushes or manual dispatch

For a full reset of the local database and storage, use the installed admin CLI directly:

```bash
.venv/bin/paperbase-dev flush-local
```

## Local Architecture

The repo has four moving pieces:

- `paperbase/` contains the backend package and both runtime entrypoints
- `paperbase-plugin/` contains the MCP bridge used by Codex and Claude Code
- `chat/` contains the React frontend
- `paperbase/src/paperbase/static/` is generated build output, not source

`paperbase-api` and `paperbase-worker` are the same codebase and image with different entrypoints:

- `paperbase-api` serves the FastAPI application
- `paperbase-worker` runs the background job loop

The browser chat experience is project-based:

- projects are persisted in the backend and own chat sessions
- the sidebar lazily loads mixed project items per project, so chats and Python files appear together
- project descriptions are snapped onto new sessions as per-session prompt context, so later project edits do not rewrite existing chats
- Python files are project-scoped tabs backed by Monaco in a high-contrast editor view
- creating or saving a Python file requires a connected GitHub account with repo access
- `Save` creates a single Paperbase-managed GitHub commit for the current project, and `Run` saves first, then executes the saved file through the user’s Modal sandbox credentials
- project repos are written with `/src` for Python files, `/notes` reserved for notes, and a root `.paperbase` metadata file
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
