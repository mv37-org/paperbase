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

They do **not** need a real Postgres database or Cloudflare R2 bucket, because the tests use in-memory substitutes and stubs.

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

## How to run the plugin MCP server locally

```bash
make run-plugin-mcp
```

That runs the shared MCP server from `paperbase-plugin/`.

## Environment variables you will care about later

For real hosted usage, `paperbase` expects settings like:

- `PAPERBASE_API_KEY`
- `PAPERBASE_DATABASE_URL`
- `PAPERBASE_R2_ENDPOINT_URL`
- `PAPERBASE_R2_ACCESS_KEY_ID`
- `PAPERBASE_R2_SECRET_ACCESS_KEY`
- `PAPERBASE_R2_BUCKET`

The plugin needs:

- `PAPERBASE_BASE_URL`
- `PAPERBASE_API_KEY`

For basic local test runs, you usually do **not** need to set these.

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
