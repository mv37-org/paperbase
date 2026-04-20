from __future__ import annotations

from fastapi.testclient import TestClient

from paperbase_chat.app import app
from paperbase_chat.models import ChatRequest, ChatTurn, PaperCard
from paperbase_chat.service import ChatService, get_chat_service


class FakeClient:
    def search_papers(self, *, query: str, limit: int, include_live_sources: bool = False) -> dict:
        assert query
        return {
            "papers": [
                {
                    "paper_id": "arxiv:2401.12345",
                    "arxiv_id": "2401.12345",
                    "title": "Grounded Paper",
                    "summary_short": "A paper about grounded evidence retrieval.",
                    "abstract": "Grounded evidence retrieval.",
                    "index_status": "indexed",
                }
            ]
        }

    def get_paper(self, paper_id: str) -> dict:
        assert paper_id == "arxiv:2401.12345"
        return {
            "paper_id": paper_id,
            "arxiv_id": "2401.12345",
            "title": "Grounded Paper",
            "summary_short": "A paper about grounded evidence retrieval.",
            "abstract": "Grounded evidence retrieval.",
            "index_status": "indexed",
        }

    def search_sections(self, *, query: str, limit: int, paper_ids: list[str]) -> dict:
        assert paper_ids == ["arxiv:2401.12345"]
        return {
            "sections": [
                {
                    "path": "sections/methods.md",
                    "chunk_kind": "section",
                    "score": 0.91,
                },
                {
                    "path": "sections/results.md",
                    "chunk_kind": "section",
                    "score": 0.77,
                },
            ]
        }

    def read_paper_file(self, paper_id: str, path: str) -> dict:
        if path == "sections/methods.md":
            return {
                "content": (
                    "The method retrieves section-level evidence from indexed papers. "
                    "It ranks passages by query overlap and section score."
                )
            }
        return {
            "content": (
                "Results show grounded answers stay tied to retrieved passages. "
                "Users can inspect which sections were used."
            )
        }


def build_service() -> ChatService:
    return ChatService(client=FakeClient())


def test_home_renders_html_shell():
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "Talk to an indexed paper." in response.text


def test_search_endpoint_returns_indexed_papers():
    app.dependency_overrides[get_chat_service] = build_service
    client = TestClient(app)

    response = client.post("/v1/papers/search", json={"query": "grounded"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["papers"][0]["paper_id"] == "arxiv:2401.12345"
    app.dependency_overrides.clear()


def test_chat_endpoint_returns_grounded_evidence():
    app.dependency_overrides[get_chat_service] = build_service
    client = TestClient(app)

    response = client.post(
        "/v1/chat",
        json=ChatRequest(
            paper_id="arxiv:2401.12345",
            question="How does the method ground the answer?",
            history=[ChatTurn(role="user", content="Tell me about the retrieval flow")],
        ).model_dump(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["paper"]["title"] == "Grounded Paper"
    assert "Best evidence from Grounded Paper" in payload["answer"]
    assert payload["evidence"][0]["path"] == "sections/methods.md"
    assert payload["rewritten_queries"]
    app.dependency_overrides.clear()
