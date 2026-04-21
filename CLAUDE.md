# Claude Instructions

Use this repository through the supported repo-level workflow, not through custom one-off command sequences.

## Use These Commands

Primary commands:

- `make setup`
- `make doctor`
- `make dev`
- `make test`
- `make profile`
- `make clean`

Lower-level workflow operations should go through `paperbase-dev`, not ad hoc scripts.

## Required Validation Flow

This repo now treats test and profile readiness as a first-class step.

For tests:

- `paperbase-dev doctor --mode test`
- `paperbase-dev init-local`
- `paperbase-dev verify-run --mode test`
- then pytest, chat checks, and timing comparison through `make test`

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

## Developer Experience Summary

The intended local experience is:

1. copy `.env.example` to `.env`
2. run `make setup`
3. run `make doctor`
4. run `make dev`

For validation:

- `make test` is the standard correctness gate
- `make profile` is the standard performance/profiling gate

Git hooks and CI are expected to delegate to these same make targets.
