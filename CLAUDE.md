# Claude Instructions

Use this repository through the supported repo-level workflow, not through custom one-off command sequences.

## Use These Commands

Primary commands:

- `make setup`
- `make doctor`
- `make dev`
- `make reset-local`
- `make test-smoke`
- `make test`
- `make profile`
- `make benchmark`
- `make clean`

Lower-level workflow operations should go through `paperbase-dev`, not ad hoc scripts. Operator-facing `paperbase-dev` commands include `benchmark list`, `benchmark run iq-1`, `jobs list-dead`, `jobs retry <job_id>`, and `harvest`; for harvests, `--limit-per-window` caps each category-window chunk, not the whole category across the requested range.

## Required Validation Flow

This repo now treats test and profile readiness as a first-class step.

For tests:

- `paperbase-dev doctor --mode test`
- `paperbase-dev init-local`
- `paperbase-dev verify-run --mode test`
- then pytest, chat checks, and timing comparison through `make test`
- pull-request CI may use `make test-smoke`, which keeps the same readiness checks but runs a focused backend subset instead of the full test suite

For profiling:

- `paperbase-dev doctor --mode profile`
- `paperbase-dev init-local`
- `paperbase-dev verify-run --mode profile`
- then deterministic hotpath profiling and SQL plan capture through `make profile`

`verify-run` checks that required env vars exist and that the initialized Postgres database has the required extensions and expected public tables before the run continues.

## Environment Rules

- use the repo-root `.env`
- start from `.env.example` if needed
- assume local PostgreSQL with `pgvector` and `pg_trgm`
- assume `vp` is required

Do not introduce a second tracked env workflow unless the repo is being intentionally redesigned.

## Tests Must Be Updated

Always update tests together with behavior changes.

Examples:

- API/auth changes: update `paperbase/tests/test_api_auth.py`
- worker changes: update `paperbase/tests/test_worker.py`
- developer workflow changes: update `paperbase/tests/test_dev_cli.py`
- repo wiring changes: update `paperbase/tests/test_repo_policy.py`
- chat runtime changes: update `chat/src/*.test.ts*`

Always run `make test` for meaningful changes before handing work off.

Run `make profile` too when the change affects:

- indexing
- search
- retrieval
- worker loops
- profiling or benchmark code
- database query hotpaths

If the expected timing/profile output changes, refresh and commit the baselines:

- `UPDATE_BASELINE=1 make test`
- `UPDATE_BASELINE=1 make profile`

## Documentation Must Be Updated

Always update documentation when commands, behavior, architecture, setup, validation, or expectations change.

Check at least:

- `README.md`
- `AGENTS.md`
- `CLAUDE.md`
- any relevant package README in `paperbase/`, `paperbase-plugin/`, or `chat/`

Do not leave workflow changes undocumented.

## Project Code Agent Scope

Browser-chat coding flows are project-scoped, not repo-shell scoped.

- operate on project-scoped repo-relative workspace text files rather than arbitrary paths in this repo
- read target files before editing and prefer exact-match patch edits over broad rewrites
- default generated ML code to PyTorch unless the user asks for something else
- keep runnable entrypoints compatible with `--run-mode smoke|full`

## Developer Experience Summary

The intended local experience is:

1. copy `.env.example` to `.env`
2. run `make setup`
3. run `make doctor`
4. run `make dev`

For validation:

- `make test` is the standard correctness gate
- `make test-smoke` is the cheaper PR CI gate
- `make profile` is the standard performance/profiling gate

Git hooks still use `make test`. PR CI uses `make test-smoke`, while `main` and manual verify runs use `make test`.
