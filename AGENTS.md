# Agent Instructions

This repository has a single supported developer experience. Do not invent alternate setup or validation flows when working here.

## Core Workflow

Use the repo-root `Makefile` as the primary interface:

- `make setup`
- `make doctor`
- `make dev`
- `make reset-local`
- `make test`
- `make test-smoke` (PR CI smoke gate)
- `make profile`
- `make benchmark`
- `make clean`

Do not replace these with ad hoc command sequences in normal work. If you need lower-level helpers, use the installed `paperbase-dev` CLI rather than custom shell scripts.

Operator-facing `paperbase-dev` commands include `benchmark list`, `benchmark run iq-1`, `jobs list-dead`, `jobs retry <job_id>`, `jobs cancel <job_id>`, and `harvest`. For harvests, `--limit-per-window` caps each category-window chunk, not the whole category across the requested range.

## Environment and Local Prerequisites

The only supported local env file is the repo-root `.env`, usually created from `.env.example`.

Required tools and services:

- Python 3.11+
- `vp`
- local PostgreSQL with `pgvector` and `pg_trgm`

Before tests or profiling, the repo now expects explicit readiness checks:

- `make test` runs:
  - `paperbase-dev doctor --mode test`
  - `paperbase-dev init-local`
  - `paperbase-dev verify-run --mode test`
  - chat Vitest in single-worker mode with both console and JSON reporting so CI surfaces frontend failures clearly
- `make test-smoke` runs:
  - the same doctor / init-local / verify-run readiness checks as `make test`
  - `ruff check`, `vp check`, frontend Vitest, and a frontend build
  - a focused backend subset (`test_api_auth`, `test_dev_cli`, `test_repo_policy`, `test_service`) plus plugin tests
  - it skips the full backend suite and timing comparison step, and is intended for PR CI rather than the default local correctness gate
- `make profile` runs:
  - `paperbase-dev doctor --mode profile`
  - `paperbase-dev init-local`
  - `paperbase-dev verify-run --mode profile`

`verify-run` is the contract that checks required env vars and validates that the target database has the expected extensions and tables before pytest or profiling begins.

## Testing Policy

Always update tests when behavior changes.

Minimum expectations:

- code changes: add or update the relevant backend, frontend, or integration tests
- workflow changes: update `paperbase/tests/test_dev_cli.py` and any repo-policy coverage
- API/auth changes: update `paperbase/tests/test_api_auth.py`
- worker changes: update `paperbase/tests/test_worker.py`
- chat runtime changes: update `chat/src/*.test.ts*`

Always run the appropriate verification before you stop:

- default for meaningful changes: `make test`
- PR CI uses `make test-smoke`, but local completion and pre-push validation still expect `make test`
- performance-sensitive or retrieval/indexing/worker hotpath changes: `make profile`

If a test or profile baseline is intentionally changed, refresh the committed baselines with:

- `UPDATE_BASELINE=1 make test`
- `UPDATE_BASELINE=1 make profile`

Baseline artifacts live in `benchmarks/baselines/` and are meant to be checked into git.

## Documentation Policy

Always update documentation when behavior, workflow, commands, environment requirements, or architecture changes.

At minimum, consider:

- `README.md`
- `AGENTS.md`
- `CLAUDE.md`
- service-specific READMEs under `paperbase/`, `paperbase-plugin/`, or `chat/`

If you change the developer workflow, keep the docs synchronized in the same change.

## Local Development Notes

`make dev` runs the full local stack:

- API: `http://127.0.0.1:8080`
- chat dev server: `http://127.0.0.1:5173`
- MCP bridge: `http://127.0.0.1:8090/mcp`

Logs are written to `.run/`.

The API reload watcher is limited to authored backend source. Generated paper storage and packaged frontend static output must not trigger API reloads.

Generated frontend build output lives in `paperbase/src/paperbase/static/`. Treat that directory as build output, not authored source.

## Git and Review Expectations

Keep commits focused and frequent.

Before opening or updating a PR:

- ensure the branch is pushed
- ensure `make test` passes
- run `make profile` when the change touches hotpaths or performance-sensitive flows
- include notes about any updated baselines, profiling deltas, or known residual risks

If you change the supported workflow itself, mention that explicitly in the PR notes.
