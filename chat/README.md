# paperbase-chat

`paperbase-chat` is the browser product in this repo. It keeps `paperbase` as
the backend API and uses a separate React frontend in `chat/frontend/` built
with VitePlus.

The important split is:

- `paperbase/` owns auth, sessions, uploads, retrieval, and chat APIs
- `paperbase-plugin/` stays unchanged
- `chat/` serves the built frontend bundle and the lightweight FastAPI shell

## Frontend stack

The chat UI now lives in:

- `chat/frontend/` for React, Slate, and VitePlus source code
- `chat/src/paperbase_chat/static/` for the built assets served by FastAPI

VitePlus builds directly into the Python package's static directory, so the
browser app can be shipped without changing the backend API shape.

## Run it locally

Start the backend stack first:

```bash
make start-local
```

Then, in another terminal:

```bash
make run-chat
```

That target installs the frontend dependencies, builds the React app into
`chat/src/paperbase_chat/static/`, and starts the chat server on
[http://127.0.0.1:8091](http://127.0.0.1:8091).

If you only want to rebuild the frontend bundle:

```bash
make build-chat-frontend
```

## Environment

`paperbase-chat` reads:

- `PAPERBASE_BASE_URL` for the backend base URL
- `PAPERBASE_API_KEY` for the backend bearer token
- `PAPERBASE_CHAT_HOST` for the bind host
- `PAPERBASE_CHAT_PORT` for the bind port

The defaults match the local stack from the repo `Makefile`.
