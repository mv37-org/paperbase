# paper-ai

This repo has **two boxes**:

- `paperbase/` = the server box
- `paperbase-plugin/` = the plugin box for Codex and Claude Code

If you are 5 years old:

- `paperbase` is the librarian.
- `paperbase-plugin` is the walkie-talkie you use to ask the librarian questions.

The librarian knows how to:

- find papers
- index papers
- break a paper into files like text, figures, and tables
- save that stuff so tools can read it later

The plugin knows how to:

- talk to the server over HTTP
- expose MCP tools like `search_papers`, `index_paper`, and `get_figure`
- let Codex and Claude Code use the same backend

## What “index a paper” means

It does **not** mean “train a model.”

It means:

1. grab the paper from arXiv
2. pull out useful pieces
3. save them in a predictable folder-like structure

Examples of saved artifacts:

- full text
- sections
- figures
- figure captions
- tables
- raw source or PDF
- a manifest saying what was found

## The easiest way to use this repo

Use `make`.

First, create the local Python environment and install both products:

```bash
make install
```

That will:

- create `.venv/`
- install `paperbase`
- install `paperbase-plugin`
- install test dependencies

## How to run tests

Run everything:

```bash
make test
```

Run only backend tests:

```bash
make test-paperbase
```

Run only plugin tests:

```bash
make test-plugin
```

Current tests are mostly **local safety checks**. They verify things like:

- source parsing
- indexing flow
- result-set handles
- API behavior
- plugin manifest wiring
- MCP tool exposure

They do **not** need a real Postgres database or Google Cloud Storage bucket, because the tests use in-memory substitutes and stubs.

## How to run the backend locally

Start the API server:

```bash
make run-api
```

Start the background worker in another terminal:

```bash
make run-worker
```

The worker is the part that picks up indexing jobs and latest-sync jobs.

Or start the whole local stack in one shot:

```bash
make start-all
```

That starts:

- the API on `http://127.0.0.1:8080`
- the worker
- a background MCP HTTP bridge on `http://127.0.0.1:8090/mcp`

Logs and PID files go into `.run/`.

To stop everything and clean up any leftover paperbase processes:

```bash
make kill-all
```

## How to run the plugin MCP server locally

```bash
make run-plugin-mcp
```

That runs the shared MCP server from `paperbase-plugin/` over normal stdio, which is what Codex and Claude Code expect.

`make start-all` uses an HTTP transport for the plugin instead, because background daemons cannot safely use stdio MCP.

## Environment variables you will care about later

For real hosted usage, `paperbase` expects settings like:

- `PAPERBASE_API_KEY`
- `PAPERBASE_DATABASE_URL`
- `PAPERBASE_GCS_PROJECT`
- `PAPERBASE_GCS_BUCKET`
- `PAPERBASE_GCS_SERVICE_ACCOUNT_JSON`

The plugin needs:

- `PAPERBASE_BASE_URL`
- `PAPERBASE_API_KEY`

For basic local test runs, you usually do **not** need to set these.

## Deploying `paperbase` on Cloud Run

`paperbase/` now includes Cloud Run deployment files:

- `paperbase/Dockerfile`
- `paperbase/.dockerignore`
- `paperbase/cloudrun.service.yaml`

Typical flow:

```bash
gcloud builds submit paperbase \
  --tag us-central1-docker.pkg.dev/YOUR_PROJECT_ID/YOUR_REPOSITORY/paperbase-api:latest
```

Copy `paperbase/.env.example` to a real env file first, fill in the values, then deploy:

```bash
gcloud run deploy paperbase-api \
  --image us-central1-docker.pkg.dev/YOUR_PROJECT_ID/YOUR_REPOSITORY/paperbase-api:latest \
  --region YOUR_REGION \
  --env-vars-file paperbase/.env.cloudrun
```

Cloud Run already injects `PORT`, and the API entrypoint uses that automatically.

For GCS access on Cloud Run, prefer attaching a service account with bucket permissions instead of stuffing a JSON key into an env var.

For trigger-based continuous deployment with a custom Cloud Build service account, use:

- build config: `paperbase/cloudbuild.yaml`
- included files glob: `paperbase/**`

That build config sets `options.logging: CLOUD_LOGGING_ONLY`, which avoids the Cloud Build error you hit when a trigger uses a user-specified build service account.

## Repo layout

```text
paper-ai/
├── Makefile
├── README.md
├── paperbase/
│   ├── src/paperbase/
│   └── tests/
└── paperbase-plugin/
    ├── .codex-plugin/
    ├── .claude-plugin/
    ├── src/paperbase_plugin/
    ├── skills/
    └── tests/
```

## Handy commands

Show available commands:

```bash
make help
```

Install only the backend:

```bash
make install-paperbase
```

Install only the plugin:

```bash
make install-plugin
```

Clean local cache files:

```bash
make clean
```

## One important thing to know

This refactor is a **breaking change** from the old `ai_paperclip` layout.

There is no old CLI anymore.

The new model is:

- backend service in `paperbase/`
- shared plugin in `paperbase-plugin/`
