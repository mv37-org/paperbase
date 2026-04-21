"""FastAPI entrypoint for the lightweight paper chat app."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .config import ChatSettings, get_settings


_PACKAGE_DIR = Path(__file__).resolve().parent
_STATIC_DIR = _PACKAGE_DIR / "static"

app = FastAPI(
    title="paperbase-chat",
    version="0.2.0",
    description="Public-facing chat product for paperbase",
)
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def home() -> HTMLResponse:
    settings: ChatSettings = get_settings()
    html = (_STATIC_DIR / "index.html").read_text(encoding="utf-8")
    html = html.replace("__PAPERBASE_BASE_URL__", settings.paperbase_base_url.rstrip("/"))
    return HTMLResponse(html)


def main() -> None:
    import uvicorn

    settings: ChatSettings = get_settings()
    uvicorn.run(
        "paperbase_chat.app:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )
