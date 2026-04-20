"""FastAPI entrypoint for the lightweight paper chat app."""

from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.responses import HTMLResponse

from .config import ChatSettings, get_settings
from .models import ChatRequest, ChatResponse, PaperCard, PaperSearchRequest, PaperSearchResponse
from .service import ChatService, get_chat_service
from .ui import render_app_shell


app = FastAPI(
    title="paperbase-chat",
    version="0.1.0",
    description="Lightweight chat UI for grounded conversations with indexed papers in paperbase",
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def home() -> HTMLResponse:
    return HTMLResponse(render_app_shell())


@app.post("/v1/papers/search", response_model=PaperSearchResponse)
def search_papers(request: PaperSearchRequest, service: ChatService = Depends(get_chat_service)) -> PaperSearchResponse:
    papers = service.search_papers(request.query)
    return PaperSearchResponse(papers=papers)


@app.get("/v1/papers/{paper_id}", response_model=PaperCard)
def get_paper(paper_id: str, service: ChatService = Depends(get_chat_service)) -> PaperCard:
    return service.get_paper(paper_id)


@app.post("/v1/chat", response_model=ChatResponse)
def chat(request: ChatRequest, service: ChatService = Depends(get_chat_service)) -> ChatResponse:
    return service.answer(request)


def main() -> None:
    import uvicorn

    settings: ChatSettings = get_settings()
    uvicorn.run(
        "paperbase_chat.app:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )
