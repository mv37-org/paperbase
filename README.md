# paper-ai

`paper-ai` has one supported local workflow:

1. copy the repo-root `.env.example` to `.env`
2. run `make setup`
3. run `make doctor`
4. run `make dev`

The repo is intentionally opinionated now:

- one canonical env file at the repo root
- five public `make` commands only
- `vp` is required
- Git hooks run the same verification path as CI before push

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

## Commands

`make help` shows the supported local interface:

- `make setup` installs the Python packages, chat dependencies, repo hooks, and a starter `.env`
- `make doctor` verifies `vp`, Python, Postgres, `pgvector`, ports, and the repo env
- `make dev` initializes the local database and storage, then runs the API, worker, MCP bridge, and chat dev server
- `make test` runs `ruff check`, `vp check`, a frontend build, backend tests, and plugin tests
- `make clean` stops local services and removes generated logs, caches, and frontend output

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
- the sidebar lazily loads session summaries per project in pages of five
- project descriptions are snapped onto new sessions as per-session prompt context, so later project edits do not rewrite existing chats

`make dev` runs all of these locally:

- API at `http://127.0.0.1:8080`
- chat dev server at `http://127.0.0.1:5173`
- MCP bridge at `http://127.0.0.1:8090/mcp`

API, worker, and plugin logs go to `.run/`.

## Frontend Output

The frontend bundle served by `paperbase` is built from `chat/` into `paperbase/src/paperbase/static/`.

- that directory is generated and ignored by Git
- `make test` builds it before backend tests because the API serves those files
- if you need the built shell without the dev server, run `cd chat && vp build`

## Deploy Notes

Cloud Run stays split by service, even though the codebase is shared:

- `paperbase-api` deploys the API entrypoint
- `paperbase-worker` deploys the worker entrypoint

Use the single repo-root env template as the source of truth, then apply the relevant sections to each Cloud Run service instead of maintaining separate tracked env-example files.
