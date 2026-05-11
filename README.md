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
- `PAPERBASE_GITHUB_*` powers the optional in-app GitHub connection flow and project repo sync
- `PAPERBASE_ADMIN_EMAILS` is a comma-separated allowlist for the signed-in admin jobs workspace
- `PAPERBASE_WORKER_STATUS_URL` lets the API read worker `/health` and `/metrics` so the admin jobs table can surface `stalled` runs
- `PAPERBASE_FETCH_CODE_CROSSLINKS_AT_INDEX` controls the default-on Hugging Face paper-page lookup that prefers an exact arXiv-id `githubRepo` match over raw text extraction; turn it off for pure text-only harvests
- logged-in users can store Anthropic keys, Modal sandbox credentials, a signed-in-only light/dark appearance preference, and a weekly papers review email preference

## Commands

`make help` shows the supported local interface:

- `make setup` installs the Python packages, chat dependencies, repo hooks, and a starter `.env`
- `make doctor` verifies `vp`, Python, Postgres, `pgvector`, ports, and the repo env
- `make dev` initializes the local database and storage, then runs the API, worker, MCP bridge, and chat dev server; the API auto-reloads authored backend code changes during local development without watching generated storage or frontend static output
- `make reset-local` stops the local stack, wipes the local database/storage, and recreates the latest schema
- `make test-smoke` runs the PR-oriented CI smoke gate: lint, chat checks/tests/build, a focused backend subset, and plugin tests
- `make test` runs `ruff check`, `vp check`, a frontend build, backend tests, and plugin tests
- `make profile` runs the deterministic hotpath profiling flow used for performance tracking
- `make benchmark BENCHMARK=iq-1 BENCHMARK_ARGS="--verbose"` runs a repo-native benchmark through the supported benchmark workflow
- `make clean` stops local services and removes generated logs, caches, and frontend output

## CI

- pull requests run `make test-smoke`
- pushes to `main` and manual verify dispatches run full `make test`
- verify and profile workflows cancel superseded in-progress runs on the same PR or branch
- profiling runs only on `main` pushes or manual dispatch

## Benchmarks

- deterministic hotpath and test-time artifacts live under `benchmarks/`
- [benchmarks/impact-scorecard-spec.md](./benchmarks/impact-scorecard-spec.md) defines the scientific measurement spec for indexing quality, retrieval quality, and harness lift with vs without Paperbase
- `make benchmark BENCHMARK=iq-1 BENCHMARK_ARGS="--verbose"` is the easiest way to run the first implemented scorecard slice
- `.venv/bin/paperbase-dev benchmark list` shows the available repo-native benchmarks
- `.venv/bin/paperbase-dev benchmark run iq-1 --verbose` runs the first implemented scorecard slice: a current-snapshot indexing census that writes `benchmarks/out/iq1.current.json` and `benchmarks/out/iq1.current.md`
- `.venv/bin/paperbase-dev benchmark iq-1 --verbose` remains as a compatibility alias for the direct IQ-1 runner
- benchmark runs use a `rich`-backed live terminal UI during local dev and automatically fall back to plain text when the richer UI is unavailable

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
.venv/bin/paperbase-dev jobs cancel <job_id>
```

Job responses include both raw `status` and server-computed `effective_status`. A running job is `stalled` when its worker lease expires without a heartbeat; pending jobs cancel immediately, while running jobs move through cooperative cancellation.

Code-url cleanup can be re-run with the same admin CLI:

```bash
.venv/bin/paperbase-dev backfill-code-urls --dry-run
.venv/bin/paperbase-dev backfill-code-urls
```

The backfill uses Hugging Face paper search's exact arXiv-id match before
falling back to the in-text GitHub ranker.

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
- creating a new project is local-first; GitHub sync is optional and can be enabled in Settings when users want repository backup, imports, or external version control
- when GitHub sync is enabled, the new-project modal can refresh the accessible repo list, link to GitHub's Paperbase access settings, and expose optional inference/sandbox credential fields
- synced existing repos expose a branch picker and optional import selection for `.py` and `.ipynb` files; new synced repos start on a fixed `main` branch
- the sidebar lazily loads mixed project items per project, persists the last selected project across reloads, and reopens with only that project tree expanded by default
- each project can start paper explorations from a command-palette style search over paper title or arXiv id
- paper explorations and PDF modals support text highlights and pen-drawn region selections with cropped image context for grounded chat follow-ups
- project home can generate a cached presentation with a React/D3 visual, Markdown summary, optional public share page, and optional ElevenLabs two-speaker audio walkthrough when the user has saved an ElevenLabs API key
- the product exposes a public-readable `Trending papers` feed of fully indexed and enriched papers, with a landing-page top-10 `Latest`/`Hottest` preview and 20-paper signed-in feed pages by publication date or upvotes; signed-in users can upvote, comment, and reply, and allowlisted admins also get a non-tabbed `Admin panel` jobs workspace
- the empty first-chat state now shows a dismissible setup checklist for optional GitHub sync, inference, and sandbox access
- project descriptions are snapped onto new sessions as per-session prompt context, so later project edits do not rewrite existing chats
- once the first user prompt lands in a `New chat` thread, Paperbase renames the session to a compact 3-5 word summary and keeps that title for later turns
- Python and notebook files open as the single active project resource in a Monaco-backed high-contrast editor view
- project code files can be renamed or deleted directly from the sidebar Code section, projects themselves can be deleted from the project menu including the default `General` project, and if a user has no projects left the app drops into a create-project-only state until a new project is created
- project chats can invoke a project-scoped coding tool surface against repo-relative workspace text files, including list/read/grep/write/patch/move/delete/git-diff/exec/test/lint/run operations instead of raw repo-shell access
- research turns expose a single restricted `paperbase_shell` literature tool for `search`, `results`, `ls`, `cat`, `head`, `grep`, and `map` over virtual paper paths; the MCP bridge exposes the same shell plus operational job/index tools
- coding turns auto-persist dirty project Python drafts before the agent runs tools, so the backend tool view stays aligned with Monaco edits
- duplicate Python basenames are disambiguated with repo-path context in the sidebar and active-resource title
- assistant commentary is now prompted before non-trivial tool batches, then collapsed behind a `Worked for ...` summary once the final answer arrives so session-level actions stay attached to the conclusion
- the fixed Conversation/Research papers thread header stays out of the first-chat empty and pending states, then appears once an assistant reply exists; follow-up prompts anchor the new turn at the top of the viewport so earlier turns scroll upward out of focus
- streamed and finished chat code blocks use matching light/dark editor-style themes, show line numbers, let longer snippets collapse in place with a compact chevron control, and render inline/block LaTeX formulas in chat markdown
- project Python files written by coding-tool turns refresh back into the sidebar Code section once the turn completes
- local `localhost` chat threads render a compact debug strip with the current user, project, thread, and latest message ids so Codex can inspect backend state from a pasted bundle without displacing the empty-chat layout
- notebooks are converted with Jupytext to `py:percent` source for editing/running, then regenerated back to `.ipynb` when saved to GitHub
- signed-in pages support a persisted appearance setting, so the sidebar, chat shell, markdown surfaces, drawers, and modals can switch between light and dark mode without affecting the public landing page
- allowlisted admin emails also get an Admin panel jobs workspace for creating one-off indexing/backfill jobs, monitoring env-driven latest-sync runs, searching papers by paper id/arXiv id/title/author, drilling into tracked job papers for every paper-touching worker job, inspecting job payloads / Cloud Run log filters / stored failure traces, and browsing paper summaries, code state, job touches, and stored artifacts through the browser session
- the admin tracked-papers drilldown now paginates per job run instead of loading the entire tracked paper list at once
- creating, importing, and saving project files works locally without GitHub; enabling GitHub sync adds repository import/export and conflict handling
- the coding agent is PyTorch-first for generated ML code, uses a Codex-style single turn loop with `tool_choice=auto` that continues while the model emits tool follow-ups rather than stopping at a fixed sampling-step cap, prefers reading and patching existing workspace files before broad rewrites, keeps final answers brief and grounded in cited research-paper evidence for in-scope turns, uses uploaded-document evidence only when explicitly requested, briefly redirects off-topic requests back to AI research or project code, can edit repo-relative text files beyond just managed `.py` sources, keeps turn-local diff and exec-session state visible to the model during long turns and compaction, streams final answers live instead of buffering them until the end, and can run stateful project commands with follow-up wait/log/cancel steps alongside compatibility `project_run` smoke/full entrypoints
- `Save` stores the current project file locally by default; when GitHub sync is enabled it can create a single Paperbase-managed GitHub commit, while `Run` saves first and then executes the full saved project workspace through the user’s Modal sandbox credentials with `full` mode by default
- project sync writes only Paperbase-managed paths tracked in `.paperbase`, preserving unrelated files in existing repositories
- if GitHub has moved ahead since the last project save, Paperbase opens a Monaco conflict-resolution flow before creating the next commit
- @-mentioning a paper in the chat composer sends the paper's id with the request; if the row is not yet at `index_status='indexed'` the send path synchronously fetches and indexes it from arXiv before the chat turn runs, with `mention_indexing_*` SSE events surfacing fetch progress on the streaming endpoint
- user-facing paper surfaces only expose fully indexed rows: search, mention suggestions, related-paper lookups, paper detail reads, and artifact reads all require `index_status='indexed'`; discovered rows remain ingestion/job bookkeeping until they are indexed

`make dev` runs all of these locally:

- API at `http://127.0.0.1:8080`
- chat dev server at `http://127.0.0.1:5173`
- MCP bridge at `http://127.0.0.1:8090/mcp`

API, worker, and plugin logs go to `.run/`.
When `make dev` is running, backend route and handler changes should reload into `paperbase-api` automatically without a manual restart. Generated paper storage and packaged frontend static files are excluded from the API reload watcher so long-running streams are not interrupted by harvested artifacts or chat builds.

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

Daily latest-paper sync is triggered by Cloud Scheduler calling the existing API service:

- target: `POST /v1/internal/scheduler/sync/latest`
- schedule: `0 3 * * *` in `America/Los_Angeles`
- auth: Cloud Scheduler OIDC token from `PAPERBASE_SCHEDULER_SERVICE_ACCOUNT_EMAIL`
- audience: `PAPERBASE_SCHEDULER_AUDIENCE`

Keep `PAPERBASE_WORKER_AUTO_SYNC_LATEST=false` and `PAPERBASE_WORKER_INITIAL_SYNC_LATEST=false`
on both API and worker in deployed environments. The scheduler endpoint is the automatic
enqueue source; `paperbase-worker` only consumes queued jobs. The worker should deploy with
Cloud Run CPU always allocated (`--no-cpu-throttling`) so queued work continues outside HTTP
request handling.

Example production scheduler setup:

```bash
PROJECT_ID="sudo-476714"
REGION="us-central1"
SCHEDULER_SA="paperbase-scheduler@${PROJECT_ID}.iam.gserviceaccount.com"
SCHEDULER_AUDIENCE="https://paperbase.mv37.org"
SCHEDULER_URI="https://paperbase.mv37.org/v1/internal/scheduler/sync/latest"

gcloud iam service-accounts create paperbase-scheduler --project="${PROJECT_ID}"
gcloud run services update paperbase-api \
  --region="${REGION}" \
  --update-env-vars="PAPERBASE_WORKER_AUTO_SYNC_LATEST=false,PAPERBASE_WORKER_INITIAL_SYNC_LATEST=false,PAPERBASE_SCHEDULER_SERVICE_ACCOUNT_EMAIL=${SCHEDULER_SA},PAPERBASE_SCHEDULER_AUDIENCE=${SCHEDULER_AUDIENCE}"
gcloud run services update paperbase-worker \
  --region="${REGION}" \
  --no-cpu-throttling \
  --update-env-vars="PAPERBASE_WORKER_AUTO_SYNC_LATEST=false,PAPERBASE_WORKER_INITIAL_SYNC_LATEST=false"
gcloud scheduler jobs create http paperbase-sync-latest-daily \
  --location="${REGION}" \
  --schedule="0 3 * * *" \
  --time-zone="America/Los_Angeles" \
  --uri="${SCHEDULER_URI}" \
  --http-method=POST \
  --oidc-service-account-email="${SCHEDULER_SA}" \
  --oidc-token-audience="${SCHEDULER_AUDIENCE}"
```

Use the single repo-root env template as the source of truth, then apply the relevant sections to each Cloud Run service instead of maintaining separate tracked env-example files.
