# chat

`chat/` is the frontend workspace for the Paperbase browser product. It is a
pure React + VitePlus app with no Python wrapper.

The important split is:

- `paperbase/` owns auth, sessions, uploads, retrieval, and chat APIs
- `paperbase-plugin/` stays unchanged
- `chat/` contains the frontend source only

## Frontend stack

The chat UI now lives in:

- `chat/src/` for React, Slate, and VitePlus source code
- `paperbase/src/paperbase/static/` for the built assets served by `paperbase`

VitePlus builds directly into the `paperbase` package's static directory so the
main backend can serve the app.

## Run it locally

Start the backend stack first:

```bash
make start-local
```

To run the frontend dev server in another terminal:

```bash
make run-chat
```

That starts VitePlus on [http://127.0.0.1:5173](http://127.0.0.1:5173).
The backend API and browser auth endpoints still come from `paperbase`.

If you want `paperbase` to serve the built app directly, rebuild the frontend bundle:

```bash
make build-chat-frontend
```

## Environment

The frontend reads:

- `PAPERBASE_BASE_URL` for the backend base URL
- `VITE_PAPERBASE_BASE_URL` as an optional Vite dev fallback when the HTML placeholder is not replaced by `paperbase`

The default local dev setup points at `http://127.0.0.1:8080`.
