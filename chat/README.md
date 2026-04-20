# paperbase-chat

`paperbase-chat` is a lightweight web app for asking grounded questions about
papers that are already indexed in `paperbase`.

It does not add a second paper store.
It sits on top of the existing `paperbase` API and uses:

- paper search to pick an indexed paper
- section search to find relevant passages
- file reads to pull the strongest evidence into the chat reply

## What the MVP does

- search indexed papers already stored in `paperbase`
- open a single paper and ask follow-up questions about it
- answer with grounded excerpts from the paper
- show which sections were used for each reply

The answers are intentionally evidence-first. This is a thin chat shell over
the artifacts `paperbase` already knows how to serve.

## Run it locally

Start the backend stack first:

```bash
make start-local
```

Then, in another terminal:

```bash
make run-chat
```

By default the chat app runs on [http://127.0.0.1:8091](http://127.0.0.1:8091).

## Environment

`paperbase-chat` reads:

- `PAPERBASE_BASE_URL` for the backend base URL
- `PAPERBASE_API_KEY` for the backend bearer token
- `PAPERBASE_CHAT_HOST` for the bind host
- `PAPERBASE_CHAT_PORT` for the bind port

The defaults match the local stack from the repo `Makefile`.
