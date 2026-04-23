from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from paperbase.api import _resolve_bind_address, app
from paperbase.browser_code import (
    GitHubCommitInfo,
    GitHubConflictFile,
    GitHubRepoInfo,
    GitHubSyncConflictError,
    ModalRunResult,
)
from paperbase.config import Settings, get_settings
from paperbase.extractors import ExtractionOutput, FigureArtifact, RawArtifact, SectionArtifact, TableArtifact
from paperbase.models import (
    ChatCitation,
    CreateChatThreadRequest,
    CreateProjectRequest,
    MoveChatThreadProjectRequest,
    PaperMetadata,
    ProjectRepoSyncRequest,
    SaveAnthropicKeyRequest,
    SaveModalCredentialsRequest,
    SearchRequest,
    SearchSectionRequest,
    SendChatMessageRequest,
    SignupRequest,
    UpdateAppearanceRequest,
    UpdateNotificationsRequest,
    UpdateProjectRequest,
)
from paperbase.repository import MemoryPaperRepository
from paperbase.service import PaperbaseService, _sanitize_inline_citations, get_service
from paperbase.storage import MemoryObjectStore


class FakeEmailSender:
    def __init__(self) -> None:
        self.reset_emails: list[tuple[str, str]] = []

    def send_password_reset(self, *, email: str, reset_link: str) -> None:
        self.reset_emails.append((email, reset_link))


class FakeChatModel:
    def __init__(self) -> None:
        self.create_calls: list[dict[str, object]] = []
        self.stream_calls: list[dict[str, object]] = []
        self.stream_chunks: list[str] = ["Grounded ", "answer with citations [S1]"]
        self.fail_stream_after_chunks: int | None = None

    def create_message(self, *, api_key: str, messages, system_prompt: str, tools=None, tool_choice=None):
        self.create_calls.append(
            {
                "api_key": api_key,
                "messages": messages,
                "system_prompt": system_prompt,
                "tools": tools,
                "tool_choice": tool_choice,
            }
        )
        if tools:
            tool_names = [tool.get("name") for tool in tools if isinstance(tool, dict)]
            has_tool_results = any(
                message.get("role") == "user"
                and isinstance(message.get("content"), list)
                and any(block.get("type") == "tool_result" for block in message.get("content"))
                for message in messages
                if isinstance(message, dict)
            )
            if tool_choice == {"type": "any"} or not has_tool_results:
                raw_query = next(
                    (
                        str(message.get("content"))
                        for message in reversed(messages)
                        if isinstance(message, dict)
                        and message.get("role") == "user"
                        and isinstance(message.get("content"), str)
                    ),
                    "grounding",
                )
                query_words = [
                    token.strip(".,!?():;").lower() for token in raw_query.split() if len(token.strip(".,!?():;")) > 3
                ]
                if any(word.startswith("ground") for word in query_words):
                    query = "grounding"
                elif "methods" in query_words:
                    query = "methods"
                else:
                    query = " ".join(query_words[:2]) or raw_query
                tool_name = (
                    "paperbase_search_sections"
                    if "paperbase_search_sections" in tool_names
                    else "paperbase_search"
                    if "paperbase_search" in tool_names
                    else str(tool_names[0])
                )
                return {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_search_sections",
                            "name": tool_name,
                            "input": {"query": query, "limit": 3},
                        }
                    ],
                    "stop_reason": "tool_use",
                }
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Grounded answer with citations [S1]\n\nAPI key used: {api_key[:6]}",
                }
            ],
            "stop_reason": "end_turn",
        }

    def stream_message(self, *, api_key: str, messages, system_prompt: str, tools=None, tool_choice=None):
        self.stream_calls.append(
            {
                "api_key": api_key,
                "messages": messages,
                "system_prompt": system_prompt,
                "tools": tools,
                "tool_choice": tool_choice,
            }
        )
        for index, chunk in enumerate(self.stream_chunks):
            if self.fail_stream_after_chunks is not None and index >= self.fail_stream_after_chunks:
                raise RuntimeError("stream blew up")
            yield chunk


class FakeGitHubRepoManager:
    def __init__(self) -> None:
        self.ensure_repo_calls: list[dict[str, object]] = []
        self.sync_project_files_calls: list[dict[str, object]] = []
        self.next_sync_conflict: GitHubSyncConflictError | None = None

    def ensure_repo(self, **kwargs) -> GitHubRepoInfo:
        self.ensure_repo_calls.append(kwargs)
        return GitHubRepoInfo(
            repo_name="paperbase-general-123abc",
            repo_url="https://github.com/octocat/paperbase-general-123abc",
            default_branch="main",
        )

    def sync_project_files(self, **kwargs) -> GitHubCommitInfo:
        self.sync_project_files_calls.append(kwargs)
        if self.next_sync_conflict is not None:
            conflict = self.next_sync_conflict
            self.next_sync_conflict = None
            raise conflict
        return GitHubCommitInfo(
            repo_name="paperbase-general-123abc",
            repo_url="https://github.com/octocat/paperbase-general-123abc",
            default_branch="main",
            commit_sha="abc123",
            commit_url="https://github.com/octocat/paperbase-general-123abc/commit/abc123",
        )


class FakeModalRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def run(self, *, token_id: str, token_secret: str, code: str) -> ModalRunResult:
        self.calls.append({"token_id": token_id, "token_secret": token_secret, "code": code})
        started = datetime(2026, 4, 22, 0, 0, tzinfo=timezone.utc)
        finished = datetime(2026, 4, 22, 0, 0, 1, tzinfo=timezone.utc)
        return ModalRunResult(
            status="completed",
            stdout="runner output",
            stderr="",
            exit_code=0,
            started_at=started,
            finished_at=finished,
        )


def _parse_sse_events(body: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in body.strip().split("\n\n"):
        event_name = ""
        payload = ""
        for line in block.splitlines():
            if line.startswith("event:"):
                event_name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                payload = line.split(":", 1)[1].strip()
        if event_name and payload:
            events.append((event_name, json.loads(payload)))
    return events


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {get_settings().api_key}"}


@pytest.fixture
def service() -> PaperbaseService:
    settings = Settings(
        storage_backend="memory",
        database_url="memory://",
        api_key="test-token",
        summary_provider="heuristic",
    )
    return PaperbaseService(
        settings=settings,
        repository=MemoryPaperRepository(),
        object_store=MemoryObjectStore(),
    )


@pytest.fixture
def browser_service() -> tuple[PaperbaseService, FakeEmailSender, FakeChatModel]:
    email_sender = FakeEmailSender()
    chat_model = FakeChatModel()
    settings = Settings(
        storage_backend="memory",
        database_url="memory://",
        api_key="test-token",
        anthropic_api_key="product-key-123",
    )
    service = PaperbaseService(
        settings=settings,
        repository=MemoryPaperRepository(),
        object_store=MemoryObjectStore(),
        email_sender=email_sender,
        chat_model=chat_model,
    )
    return service, email_sender, chat_model


def _sample_paper(arxiv_id: str, *, title: str, abstract: str) -> PaperMetadata:
    return PaperMetadata(
        arxiv_id=arxiv_id,
        title=title,
        authors="Alice, Bob",
        abstract=abstract,
        categories="cs.AI",
        primary_category="cs.AI",
        published="2024-01-10",
        updated="2024-01-11",
        pdf_url=f"https://arxiv.org/pdf/{arxiv_id}.pdf",
        html_url=f"https://arxiv.org/html/{arxiv_id}",
    )


def _rich_extraction(*, text: str) -> ExtractionOutput:
    return ExtractionOutput(
        source_type="html",
        text_markdown=text,
        sections=[SectionArtifact(title="Introduction", slug="introduction", markdown=text)],
        figures=[
            FigureArtifact(
                figure_id="figure-1",
                caption="A model architecture.",
                file_extension="png",
                content_type="image/png",
                image_bytes=b"PNG",
                source_url="https://arxiv.org/html/figure-1.png",
            )
        ],
        tables=[
            TableArtifact(
                table_id="table-1",
                caption="Benchmark scores",
                rows=[["Model", "Score"], ["Paperbase", "1.0"]],
            )
        ],
        raw_artifacts=[RawArtifact(path="raw/source.tar.gz", data=b"TAR", content_type="application/gzip")],
    )


def _full_text_extraction(*, text: str) -> ExtractionOutput:
    return ExtractionOutput(
        source_type="html",
        text_markdown=text,
        sections=[SectionArtifact(title="Full Text", slug="full-text", markdown=text)],
        raw_artifacts=[RawArtifact(path="raw/source.tar.gz", data=b"TAR", content_type="application/gzip")],
    )


def test_index_writes_manifest_and_artifacts(monkeypatch, service: PaperbaseService):
    paper = _sample_paper("2401.12345", title="Transformer Paper", abstract="Attention and transformers.")
    monkeypatch.setattr("paperbase.service.sources.enrich_with_semantic_scholar", lambda papers: papers)
    monkeypatch.setattr(
        "paperbase.service.index_paper_artifacts", lambda paper: _rich_extraction(text="Attention layers")
    )

    indexed = service.index_arxiv_paper(paper.arxiv_id, paper=paper)

    assert indexed.paper_id == "arxiv:2401.12345"
    assert indexed.summary_short
    assert indexed.summary_eli5.startswith("In simple terms,")
    manifest = service.get_manifest(indexed.paper_id).manifest
    assert manifest["source_type"] == "html"
    assert manifest["figure_count"] == 1
    assert manifest["table_count"] == 1

    files = {item.path for item in service.list_files(indexed.paper_id).files}
    assert "manifest.json" in files
    assert "meta.json" in files
    assert "text/full.md" in files
    assert "sections/introduction.md" in files
    assert "figures/index.json" in files
    assert "figures/figure-1/caption.md" in files
    assert "figures/figure-1/image.png" in files
    assert "tables/index.json" in files
    assert "tables/table-1.json" in files
    assert "tables/table-1.csv" in files
    assert "raw/source.tar.gz" in files


def test_reindex_replaces_stale_artifacts(monkeypatch, service: PaperbaseService):
    paper = _sample_paper("2401.33333", title="Transformer Paper", abstract="Attention and transformers.")
    monkeypatch.setattr("paperbase.service.sources.enrich_with_semantic_scholar", lambda papers: papers)

    monkeypatch.setattr(
        "paperbase.service.index_paper_artifacts", lambda paper: _full_text_extraction(text="Old extraction")
    )
    service.index_arxiv_paper(paper.arxiv_id, paper=paper, force=True)

    monkeypatch.setattr(
        "paperbase.service.index_paper_artifacts", lambda paper: _rich_extraction(text="New extraction")
    )
    service.index_arxiv_paper(paper.arxiv_id, paper=paper, force=True)

    files = {item.path for item in service.list_files("arxiv:2401.33333").files}

    assert "sections/introduction.md" in files
    assert "sections/full-text.md" not in files


def test_search_and_grep_return_result_handles(monkeypatch, service: PaperbaseService):
    monkeypatch.setattr("paperbase.service.sources.enrich_with_semantic_scholar", lambda papers: papers)
    monkeypatch.setattr(
        "paperbase.service.index_paper_artifacts",
        lambda paper: _rich_extraction(
            text=(
                "Transformer Study mentions attention and transformers"
                if "Transformer" in paper.title
                else "Vision Study mentions diffusion only"
            )
        ),
    )

    service.index_arxiv_paper(
        "2401.10001", paper=_sample_paper("2401.10001", title="Transformer Study", abstract="Transformers")
    )
    service.index_arxiv_paper(
        "2401.10002", paper=_sample_paper("2401.10002", title="Vision Study", abstract="Diffusion")
    )

    results = service.search(SearchRequest(query="transformer", include_live_sources=False))
    assert results.result_set_id.startswith("rs_")
    assert len(results.papers) == 1

    narrowed = service.grep_results(results.result_set_id, "attention", limit=10)
    assert narrowed.result_set_id != results.result_set_id
    assert narrowed.parent_result_set_id == results.result_set_id
    assert narrowed.papers[0].paper_id == "arxiv:2401.10001"

    section_hits = service.search_sections(
        SearchSectionRequest(query="attention", result_set_id=results.result_set_id, limit=10)
    )
    assert section_hits.sections
    assert section_hits.sections[0].paper_id == "arxiv:2401.10001"
    assert section_hits.sections[0].path == "sections/introduction.md"


def test_search_can_shortlist_from_generated_summaries(monkeypatch, service: PaperbaseService):
    monkeypatch.setattr("paperbase.service.sources.enrich_with_semantic_scholar", lambda papers: papers)
    monkeypatch.setattr(
        "paperbase.service.index_paper_artifacts", lambda paper: _rich_extraction(text="General workflow discussion")
    )

    summaries = {
        "2401.20001": (
            "This paper studies AI agents that use tools, plan, and act in environments.",
            "In simple terms, this paper explains how an AI agent can plan and use tools.",
        ),
        "2401.20002": (
            "This paper studies image generation systems.",
            "In simple terms, this paper explains diffusion for image generation.",
        ),
    }
    monkeypatch.setattr(
        "paperbase.service.build_paper_summaries",
        lambda paper, extraction, settings=None: summaries[paper.arxiv_id],
    )

    service.index_arxiv_paper(
        "2401.20001", paper=_sample_paper("2401.20001", title="Tool Use Study", abstract="Planning systems")
    )
    service.index_arxiv_paper(
        "2401.20002", paper=_sample_paper("2401.20002", title="Image Study", abstract="Vision systems")
    )

    results = service.search(SearchRequest(query="agent", include_live_sources=False))

    assert results.papers
    assert results.papers[0].paper_id == "arxiv:2401.20001"
    assert "AI agents" in results.papers[0].summary_short


def test_summarize_top_cited_uses_stable_paging(monkeypatch):
    service = PaperbaseService(
        settings=Settings(
            storage_backend="memory",
            database_url="memory://",
            api_key="test-token",
            summary_provider="vertex",
            featured_tier_concurrency=1,
        ),
        repository=MemoryPaperRepository(),
        object_store=MemoryObjectStore(),
    )
    monkeypatch.setattr(
        "paperbase.summaries.generate_summary_on_demand",
        lambda paper, settings: (f"Short {paper.arxiv_id}", f"ELI5 {paper.arxiv_id}"),
    )

    for index in range(70):
        paper = _sample_paper(
            f"2401.{10000 + index}",
            title=f"Paper {index}",
            abstract=f"Abstract {index}.",
        )
        paper.index_status = "indexed"
        paper.citation_count = 70 - index
        paper.influential_citation_count = 70 - index
        service.repository.upsert_paper(paper)

    result = service.summarize_top_cited(limit=70)

    assert result == {"processed": 70, "updated": 70, "failed": 0}
    assert all(paper.summary_eli5.startswith("ELI5 ") for paper in service.repository.papers.values())


def test_api_flow_for_jobs_and_results(monkeypatch, service: PaperbaseService):
    app.dependency_overrides[get_service] = lambda: service
    client = TestClient(app)
    headers = _auth_headers()
    paper = _sample_paper("2401.55555", title="Queued Paper", abstract="Queued indexing")

    monkeypatch.setattr("paperbase.service.sources.get_paper_details", lambda arxiv_id: paper)
    monkeypatch.setattr("paperbase.service.sources.enrich_with_semantic_scholar", lambda papers: papers)
    monkeypatch.setattr(
        "paperbase.service.index_paper_artifacts", lambda paper: _rich_extraction(text="Queued attention result")
    )

    job = client.post("/v1/index/papers/arxiv/2401.55555", headers=headers).json()
    assert job["status"] == "pending"

    service.process_next_job()

    manifest = client.get("/v1/papers/arxiv:2401.55555/manifest", headers=headers).json()
    assert manifest["manifest"]["index_status"] == "indexed"

    search = client.post(
        "/v1/search",
        headers=headers,
        json={"query": "queued", "limit": 10, "include_live_sources": False},
    ).json()
    section_search = client.post(
        "/v1/search/sections",
        headers=headers,
        json={"query": "attention", "limit": 10, "paper_ids": ["arxiv:2401.55555"]},
    ).json()
    grep = client.post(
        f"/v1/results/{search['result_set_id']}/grep",
        headers=headers,
        json={"pattern": "attention", "limit": 10},
    ).json()
    figure = client.get("/v1/papers/arxiv:2401.55555/figures/figure-1", headers=headers).json()
    table = client.get("/v1/papers/arxiv:2401.55555/tables/table-1", headers=headers).json()

    assert grep["parent_result_set_id"] == search["result_set_id"]
    assert section_search["sections"][0]["paper_id"] == "arxiv:2401.55555"
    assert figure["figure_id"] == "figure-1"
    assert table["table_id"] == "table-1"

    app.dependency_overrides.clear()


def test_root_serves_built_chat_shell():
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "Paperbase | MV37" in response.text
    assert 'id="root"' in response.text
    assert 'href="/static/favicon.png"' in response.text
    assert "/static/app.js" in response.text
    assert "/static/app.css" in response.text
    assert "__PAPERBASE_BASE_URL__" not in response.text


def test_root_uses_forwarded_https_base_url():
    client = TestClient(app)

    response = client.get(
        "/",
        headers={
            "x-forwarded-proto": "https",
            "x-forwarded-host": "paperbase-api.example.test",
        },
    )

    assert response.status_code == 200
    assert 'window.PAPERBASE_BASE_URL = "https://paperbase-api.example.test";' in response.text


def test_static_chat_assets_are_served():
    client = TestClient(app)

    response = client.get("/static/app.js")

    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]

    stylesheet = client.get("/static/app.css")

    assert stylesheet.status_code == 200
    assert "css" in stylesheet.headers["content-type"]

    favicon = client.get("/static/favicon.png")

    assert favicon.status_code == 200


def test_api_bind_address_prefers_explicit_port_then_platform_port(monkeypatch):
    monkeypatch.delenv("PAPERBASE_API_HOST", raising=False)
    monkeypatch.delenv("PAPERBASE_API_PORT", raising=False)
    monkeypatch.delenv("PORT", raising=False)

    assert _resolve_bind_address() == ("0.0.0.0", 8080)

    monkeypatch.setenv("PORT", "9000")
    assert _resolve_bind_address() == ("0.0.0.0", 9000)

    monkeypatch.setenv("PAPERBASE_API_HOST", "127.0.0.1")
    monkeypatch.setenv("PAPERBASE_API_PORT", "9100")
    assert _resolve_bind_address() == ("127.0.0.1", 9100)


def test_probe_integrations_returns_database_and_storage_status(service: PaperbaseService):
    probe = service.probe_integrations()

    assert probe.ok is True
    assert probe.database.ok is True
    assert probe.storage.ok is True
    assert probe.storage.metadata["path"].startswith("system/probes/probe_")
    assert probe.storage.metadata["signed_url_generated"] is True


def test_probe_integrations_route_returns_503_on_storage_failure():
    class FailingObjectStore(MemoryObjectStore):
        def put_text(self, path: str, text: str, *, content_type: str = "text/plain; charset=utf-8") -> None:
            raise RuntimeError("write denied")

    service = PaperbaseService(
        settings=Settings(storage_backend="gcs", database_url="memory://", api_key="test-token"),
        repository=MemoryPaperRepository(),
        object_store=FailingObjectStore(),
    )
    app.dependency_overrides[get_service] = lambda: service
    client = TestClient(app)

    response = client.post("/v1/debug/integrations", headers=_auth_headers())

    assert response.status_code == 503
    assert response.json()["database"]["ok"] is True
    assert response.json()["storage"]["ok"] is False
    assert "write denied" in response.json()["storage"]["detail"]

    app.dependency_overrides.clear()


class _StubEmbeddings:
    """Deterministic fake that lets reindex tests assert exactly which papers
    and chunks are processed without loading sentence-transformers."""

    def __init__(self) -> None:
        self.paper_calls: list[list[str]] = []
        self.chunk_calls: list[list[str]] = []

    def encode(self, text: str) -> list[float] | None:
        return [float(len(text)), 0.0, 0.0]

    def encode_batch(self, texts: list[str]) -> list[list[float]] | None:
        self.chunk_calls.append(list(texts))
        return [[float(i), 0.0, 0.0] for i, _ in enumerate(texts, start=1)]

    def encode_paper_batch(self, pairs: list[tuple[str, str]]) -> list[list[float]] | None:
        # Record the flattened text the reindex loop would have sent through
        # the single-string encode_batch path previously, so existing
        # assertions that inspect paper_calls keep working.
        self.paper_calls.append(["\n".join(part for part in pair if part) for pair in pairs])
        return [[float(i), 0.0, 0.0] for i, _ in enumerate(pairs, start=1)]


def _seed_indexed_paper(
    repo: MemoryPaperRepository,
    arxiv_id: str,
    *,
    with_paper_embedding: bool,
    chunks_with_embedding: int,
    chunks_without_embedding: int,
) -> str:
    paper = PaperMetadata(
        arxiv_id=arxiv_id,
        title=f"Paper {arxiv_id}",
        abstract="Abstract text for the paper.",
        published="2024-01-01",
        index_status="indexed",
        citation_count=10,
    )
    stored = repo.upsert_paper(
        paper,
        search_document=paper.abstract,
        embedding=[0.1, 0.2, 0.3] if with_paper_embedding else None,
    )
    chunks: list[dict] = []
    for index in range(chunks_with_embedding):
        chunks.append(
            {
                "path": f"sections/sec-{index}.md",
                "chunk_kind": "section",
                "content": f"Section content {arxiv_id}/{index}",
                "embedding": [0.5, 0.5, 0.5],
            }
        )
    for index in range(chunks_without_embedding):
        chunks.append(
            {
                "path": f"sections/gap-{index}.md",
                "chunk_kind": "section",
                "content": f"Uncovered content {arxiv_id}/{index}",
            }
        )
    repo.replace_text_chunks(stored.paper_id, chunks)
    return stored.paper_id


def test_reindex_visits_chunk_only_candidates(service: PaperbaseService):
    # Paper A: fully embedded — should be ignored.
    _seed_indexed_paper(
        service.repository, "2401.00001", with_paper_embedding=True, chunks_with_embedding=2, chunks_without_embedding=0
    )
    # Paper B: paper embedding present, chunks missing — the regression case.
    paper_b = _seed_indexed_paper(
        service.repository, "2401.00002", with_paper_embedding=True, chunks_with_embedding=1, chunks_without_embedding=2
    )
    # Paper C: paper embedding missing — should also be picked up.
    paper_c = _seed_indexed_paper(
        service.repository,
        "2401.00003",
        with_paper_embedding=False,
        chunks_with_embedding=0,
        chunks_without_embedding=1,
    )

    service.embeddings = _StubEmbeddings()
    result = service.reindex_missing_embeddings(limit=100, include_chunks=True)

    # Both B and C need updates; A must stay untouched.
    assert result["chunks_updated"] == 3  # 2 from B + 1 from C
    # At minimum the paper missing its vector must be refreshed; we don't
    # require B to be re-embedded, but it's allowed.
    assert result["papers_updated"] >= 1
    assert service.repository.list_chunks_missing_embedding(paper_b) == []
    assert service.repository.list_chunks_missing_embedding(paper_c) == []


def test_reindex_processes_all_pages_without_skipping(service: PaperbaseService):
    # Seed 20 papers each with one missing chunk embedding. Backfill with a
    # small per-page budget so more than one round is required.
    paper_ids: list[str] = []
    for i in range(20):
        arxiv_id = f"2402.{i:05d}"
        paper_ids.append(
            _seed_indexed_paper(
                service.repository,
                arxiv_id,
                with_paper_embedding=True,
                chunks_with_embedding=0,
                chunks_without_embedding=1,
            )
        )

    service.embeddings = _StubEmbeddings()
    # Force multiple batches through the page floor.
    service.settings.chunk_max_chars = 1500
    result = service.reindex_missing_embeddings(limit=20, include_chunks=True)

    assert result["chunks_updated"] == 20
    for paper_id in paper_ids:
        assert service.repository.list_chunks_missing_embedding(paper_id) == []


def test_reindex_rewrites_existing_embeddings_when_requested(service: PaperbaseService):
    paper_id = _seed_indexed_paper(
        service.repository,
        "2402.99999",
        with_paper_embedding=True,
        chunks_with_embedding=2,
        chunks_without_embedding=0,
    )
    original_paper_embedding = list(service.repository.paper_embeddings[paper_id])
    original_chunk_embeddings = [
        list(chunk["embedding"])
        for chunk in service.repository.list_chunks_missing_embedding(paper_id, include_existing=True)
    ]

    service.embeddings = _StubEmbeddings()
    result = service.reindex_embeddings(limit=10, include_chunks=True, rewrite_existing=True)

    assert result == {"papers_updated": 1, "chunks_updated": 2}
    assert service.repository.paper_embeddings[paper_id] != original_paper_embedding
    updated_chunk_embeddings = [
        list(chunk["embedding"])
        for chunk in service.repository.list_chunks_missing_embedding(paper_id, include_existing=True)
    ]
    assert updated_chunk_embeddings != original_chunk_embeddings


def test_reindex_batches_large_chunk_rewrites(service: PaperbaseService):
    _seed_indexed_paper(
        service.repository,
        "2402.99998",
        with_paper_embedding=True,
        chunks_with_embedding=260,
        chunks_without_embedding=0,
    )
    embeddings = _StubEmbeddings()
    service.embeddings = embeddings

    result = service.reindex_embeddings(limit=10, include_chunks=True, rewrite_existing=True)

    assert result["chunks_updated"] == 260
    assert [len(batch) for batch in embeddings.chunk_calls] == [128, 128, 4]


def test_enqueue_reindex_embeddings_job_defaults_to_full_rewrite(service: PaperbaseService):
    job = service.enqueue_reindex_embeddings_job(limit=11, include_chunks=False)

    assert job.job_type == "reindex_embeddings"
    assert job.payload == {
        "limit": 11,
        "include_chunks": False,
        "rewrite_existing": True,
    }


def test_suggest_and_stats_endpoints(service: PaperbaseService):
    _seed_indexed_paper(
        service.repository, "2403.00001", with_paper_embedding=True, chunks_with_embedding=2, chunks_without_embedding=0
    )
    response = service.suggest_papers("paper 2403", limit=5)
    assert response, "suggest should return at least the matching seed paper"
    assert response[0]["matched_field"] in {"title", "title_prefix", "authors", "summary"}

    stats = service.get_stats()
    assert stats["indexed_papers"] >= 1
    assert stats["papers_with_embedding"] >= 1
    assert stats["total_chunks"] >= 2


def test_reindex_continues_past_failed_frontier_page(service: PaperbaseService):
    """Regression for the 'top frontier fails forever' starvation bug.

    Seed 20 papers missing chunk embeddings. First ~10 attempts fail; the
    remaining ones must still be reached. Before the query-time exclusion fix
    the loop stalled as soon as every row in the top frontier was in the
    client-side `attempted` set.
    """
    paper_ids: list[str] = []
    for i in range(20):
        paper_ids.append(
            _seed_indexed_paper(
                service.repository,
                f"2404.{i:05d}",
                with_paper_embedding=True,
                chunks_with_embedding=0,
                chunks_without_embedding=1,
            )
        )

    failing_paper_ids = set(paper_ids[:10])

    class SelectiveFailEmbeddings(_StubEmbeddings):
        def encode_batch(self, texts: list[str]) -> list[list[float]] | None:
            # Every other call is a chunk call in our loop order; the chunk
            # loop iterates per-paper, so we flip based on paper identity via
            # a sentinel in the text payload. Simpler: fail if any text
            # mentions a known-to-fail paper id. (Test controls the payloads
            # via `_seed_indexed_paper`, which stamps ids into content.)
            if any(any(fid.replace("arxiv:", "") in text for fid in failing_paper_ids) for text in texts):
                return None
            return super().encode_batch(texts)

    service.embeddings = SelectiveFailEmbeddings()
    result = service.reindex_missing_embeddings(limit=20, include_chunks=True)

    # At least one of the non-failing papers past the first frontier must have
    # been reached. Exact count depends on interleaving with the paper loop,
    # but the critical property is that we advanced past the failing set.
    assert result["chunks_updated"] >= 1
    resolved_ids = [
        paper_id for paper_id in paper_ids[10:] if not service.repository.list_chunks_missing_embedding(paper_id)
    ]
    assert resolved_ids, "reindex should advance past the failing frontier"


def test_summarize_top_cited_continues_past_failed_frontier(service: PaperbaseService):
    """Regression for the same starvation pattern in summarize_top_cited.

    Seed 40 candidate papers. Stub summarization to fail for the first 20 and
    succeed for the rest. The loop must still reach and update the later
    papers.
    """
    service.settings.summary_provider = "vertex"
    service.settings.vertex_project = "stub"
    service.settings.featured_tier_concurrency = 4

    paper_ids: list[str] = []
    for i in range(40):
        paper_ids.append(
            _seed_indexed_paper(
                service.repository,
                f"2405.{i:05d}",
                with_paper_embedding=True,
                chunks_with_embedding=0,
                chunks_without_embedding=0,
            )
        )
        # Force needs-summary: wipe the heuristic summary the seed put there.
        service.repository.update_summaries(paper_ids[-1], summary_short="", summary_eli5="")

    failing = set(paper_ids[:20])

    import paperbase.summaries as summaries_module

    def fake_summary(paper, settings):
        if paper.paper_id in failing:
            return None
        return ("technical summary", "plain summary")

    original = summaries_module.generate_summary_on_demand
    summaries_module.generate_summary_on_demand = fake_summary  # type: ignore[attr-defined]
    try:
        result = service.summarize_top_cited(limit=40)
    finally:
        summaries_module.generate_summary_on_demand = original

    # Any later-ranked paper getting its summary proves the frontier advanced.
    later_updated = [
        pid
        for pid in paper_ids[20:]
        if (service.repository.get_paper(pid) or type("_", (), {"summary_eli5": ""})).summary_eli5
    ]
    assert later_updated, "summarize_top_cited should advance past failures"
    assert result["updated"] >= 1


def test_suggest_papers_matches_summary_text(service: PaperbaseService):
    """A paper whose term lives only in summary_short must still surface."""
    paper = PaperMetadata(
        arxiv_id="2406.00001",
        title="Totally unrelated headline",
        abstract="abstract text",
        summary_short="This paper investigates multi-agent cooperation under partial observability.",
        authors="Someone, Other",
        published="2024-06-01",
        index_status="indexed",
    )
    service.repository.upsert_paper(paper, search_document=paper.abstract)

    hits = service.suggest_papers("multi-agent", limit=5)
    assert hits, "summary_short match should surface the paper"
    assert hits[0]["matched_field"] == "summary"


def test_suggest_papers_returns_highlight_payload(service: PaperbaseService):
    paper = PaperMetadata(
        arxiv_id="2406.00002",
        title="Attention Is All You Need",
        abstract="abstract",
        authors="Vaswani et al.",
        published="2017-06-01",
        index_status="indexed",
    )
    service.repository.upsert_paper(paper, search_document=paper.abstract)

    hits = service.suggest_papers("Attention", limit=5)
    assert hits
    top = hits[0]
    assert "highlight_snippet" in top
    assert "Attention" in top["highlight_snippet"]


def test_suggest_fallback_papers_returns_indexed_ranked_papers(service: PaperbaseService):
    indexed_high = PaperMetadata(
        arxiv_id="2406.00003",
        title="Top Indexed Paper",
        abstract="abstract",
        summary_short="Top fallback summary",
        authors="Ada Lovelace",
        published="2026-04-01",
        citation_count=50,
        influential_citation_count=7,
        index_status="indexed",
    )
    indexed_low = PaperMetadata(
        arxiv_id="2406.00004",
        title="Lower Ranked Indexed Paper",
        abstract="abstract",
        authors="Grace Hopper",
        published="2025-04-01",
        citation_count=5,
        influential_citation_count=1,
        index_status="indexed",
    )
    pending = PaperMetadata(
        arxiv_id="2406.00005",
        title="Pending Paper",
        abstract="abstract",
        authors="Alan Turing",
        published="2026-04-02",
        citation_count=500,
        influential_citation_count=50,
        index_status="pending",
    )
    service.repository.upsert_paper(indexed_low, search_document=indexed_low.abstract)
    service.repository.upsert_paper(pending, search_document=pending.abstract)
    service.repository.upsert_paper(indexed_high, search_document=indexed_high.abstract)

    hits = service.suggest_fallback_papers(limit=5)

    assert [hit["title"] for hit in hits[:2]] == [
        "Top Indexed Paper",
        "Lower Ranked Indexed Paper",
    ]
    assert all(hit["matched_field"] == "fallback" for hit in hits)
    assert "Pending Paper" not in {hit["title"] for hit in hits}


def test_specter2_embedder_uses_text_pair_tokenization():
    """The SPECTER2 raw-transformers path must build `title [SEP] body` via
    the tokenizer's pair API, not a newline-joined single string. Regressing
    this silently drifts vectors away from the space SPECTER2 was trained on.
    """
    from paperbase.service import _TransformersCLSEmbedder

    tokenizer_calls: list[dict[str, object]] = []

    class _FakeBatch(dict):
        """Behaves like a tokenizer BatchEncoding: supports `.to()` and
        `**unpacking` into model call kwargs."""

        def to(self, device):
            return self

    class _FakeTokenizer:
        def __call__(self, *args, **kwargs):
            tokenizer_calls.append(dict(kwargs))
            return _FakeBatch(input_ids=[[0]])

    class _FakeHidden:
        def __init__(self, arr):
            self._arr = arr

        @property
        def last_hidden_state(self):
            return self

        def __getitem__(self, _idx):
            class _Tensor:
                def __init__(self, arr):
                    self._arr = arr

                def detach(self):
                    return self

                def cpu(self):
                    return self

                def numpy(self):
                    return self._arr

            return _Tensor(self._arr)

    class _FakeModel:
        def __call__(self, **_kwargs):
            class _Out:
                pass

            import numpy as np

            out = _Out()
            out.last_hidden_state = _FakeHiddenState(np.array([[0.1, 0.2, 0.3]], dtype="float32"))
            return out

        def eval(self):
            return self

        def to(self, device):
            return self

    class _FakeHiddenState:
        def __init__(self, arr):
            self._arr = arr

        def __getitem__(self, _idx):
            class _Row:
                def __init__(self, arr):
                    self._arr = arr

                def detach(self):
                    return self

                def cpu(self):
                    return self

                def numpy(self):
                    return self._arr

            return _Row(self._arr)

    embedder = _TransformersCLSEmbedder.__new__(_TransformersCLSEmbedder)
    import torch

    # Stub torch.nn.functional.normalize so _run doesn't execute real math
    # against our fake tensors.
    original_normalize = torch.nn.functional.normalize
    torch.nn.functional.normalize = lambda tensor, **_kw: tensor
    try:
        embedder._torch = torch
        embedder._tokenizer = _FakeTokenizer()
        embedder._model = _FakeModel()
        embedder._device = "cpu"

        # Pair input → tokenizer must be called with text= and text_pair=.
        embedder.encode_pairs(
            [
                ("Attention Is All You Need", "We propose a model based solely on attention."),
            ]
        )
        assert tokenizer_calls, "tokenizer should have been invoked"
        call = tokenizer_calls[-1]
        assert call.get("text") == ["Attention Is All You Need"]
        assert call.get("text_pair") == ["We propose a model based solely on attention."]
        assert call.get("max_length") == 512

        # Chunk input (no pair) → no text_pair kwarg, single-sequence path.
        tokenizer_calls.clear()
        embedder.encode(["Section: Introduction. We describe..."])
        assert tokenizer_calls
        call = tokenizer_calls[-1]
        assert "text_pair" not in call
    finally:
        torch.nn.functional.normalize = original_normalize


def test_encode_paper_drops_summary_short_from_paper_embedding_input(monkeypatch):
    """Paper-level vector input must not include summary_short. SPECTER2 was
    never trained on that heuristic-generated text; keeping it in the input
    drifts the vectors away from the training distribution.
    """
    from paperbase.config import Settings
    from paperbase.service import OptionalEmbeddingProvider

    settings = Settings(
        storage_backend="memory",
        database_url="memory://",
        api_key="test",
        summary_provider="none",
        embedding_model="allenai/specter2_base",
        embedding_dim=768,
    )
    provider = OptionalEmbeddingProvider(settings)

    received: list[tuple[str, str | None]] = []

    class _FakeSpecterModel:
        def encode_pairs(self, pairs, **_):
            received.extend(pairs)
            return [[0.0] * settings.embedding_dim for _ in pairs]

    monkeypatch.setattr(provider, "_model", lambda: _FakeSpecterModel())

    vector = provider.encode_paper(
        title="Attention Is All You Need",
        body="We propose a model based solely on attention mechanisms.",
    )
    assert vector is not None
    assert received == [("Attention Is All You Need", "We propose a model based solely on attention mechanisms.")]
    # Guard: caller builds the pair; heuristic summaries must not leak in.
    assert all("summary" not in (pair[0] or "").lower() for pair in received)
    assert all("summary" not in (pair[1] or "").lower() for pair in received)


def test_init_creates_trigram_indexes_for_summary_and_tldr():
    """Verify that SqlAlchemyPaperRepository.init() issues CREATE INDEX DDL
    for summary_short and tldr trigram ops. The suggest endpoint's widened
    ILIKE branch depends on those indexes to stay out of sequential-scan
    territory under typeahead load.
    """
    from paperbase.repository import Base, SqlAlchemyPaperRepository

    statements: list[str] = []

    class _FakeConn:
        def execute(self, stmt, *args, **kwargs):
            text_attr = getattr(stmt, "text", None)
            if text_attr is not None:
                statements.append(text_attr)

    class _FakeBegin:
        def __enter__(self):
            return _FakeConn()

        def __exit__(self, *exc):
            return False

    class _FakeEngine:
        url = None

        def begin(self):
            return _FakeBegin()

    repo = SqlAlchemyPaperRepository.__new__(SqlAlchemyPaperRepository)
    repo._embedding_dim = 768
    repo._engine = _FakeEngine()
    repo._session_factory = None  # type: ignore[assignment]
    repo._backfill_projects = lambda: None  # type: ignore[method-assign]

    original_create_all = Base.metadata.create_all
    Base.metadata.create_all = lambda engine: None
    try:
        repo.init()
    finally:
        Base.metadata.create_all = original_create_all

    combined = "\n".join(statements)
    assert "ALTER TABLE IF EXISTS papers ADD COLUMN IF NOT EXISTS embedding VECTOR(768)" in combined
    assert "ALTER TABLE IF EXISTS text_chunks ADD COLUMN IF NOT EXISTS embedding VECTOR(768)" in combined
    assert "papers_summary_short_trgm" in combined
    assert "summary_short gin_trgm_ops" in combined
    assert "papers_tldr_trgm" in combined
    assert "tldr gin_trgm_ops" in combined


def test_browser_auth_and_password_reset_flow(browser_service):
    service, email_sender, _ = browser_service
    app.dependency_overrides[get_service] = lambda: service
    client = TestClient(app)

    signup = client.post(
        "/v1/browser/auth/signup",
        json={"email": "user@example.com", "password": "topsecret123", "display_name": "User"},
    )
    assert signup.status_code == 200
    assert signup.json()["authenticated"] is True
    assert signup.json()["remaining_free_responses"] == 3
    csrf_token = signup.json()["csrf_token"]
    assert csrf_token

    session = client.get("/v1/browser/session")
    assert session.json()["user"]["email"] == "user@example.com"
    assert session.json()["csrf_token"] == csrf_token

    forgot = client.post("/v1/browser/auth/forgot-password", json={"email": "user@example.com"})
    assert forgot.status_code == 200
    assert email_sender.reset_emails
    reset_link = email_sender.reset_emails[0][1]
    reset_token = parse_qs(urlparse(reset_link).query)["reset_token"][0]

    reset = client.post("/v1/browser/auth/reset-password", json={"token": reset_token, "password": "newsecret456"})
    assert reset.status_code == 200

    logout = client.post("/v1/browser/auth/logout", headers={"X-Paperbase-CSRF": csrf_token})
    assert logout.status_code == 200

    login = client.post("/v1/browser/auth/login", json={"email": "user@example.com", "password": "newsecret456"})
    assert login.status_code == 200
    assert login.json()["authenticated"] is True
    assert login.json()["csrf_token"]

    app.dependency_overrides.clear()


def test_browser_settings_round_trip(browser_service):
    service, _, _ = browser_service
    session, _ = service.register_browser_user(
        SignupRequest(email="settings@example.com", password="topsecret123", display_name="Settings User")
    )
    assert session.user is not None

    anthropic = service.save_user_anthropic_key(
        session.user,
        SaveAnthropicKeyRequest(api_key="anthropic-user-key-999"),
    )
    modal_credentials = service.save_user_modal_credentials(
        session.user,
        SaveModalCredentialsRequest(
            token_id="modal-token-id-999",
            token_secret="modal-token-secret-999",
        ),
    )
    notifications = service.update_user_notification_preferences(
        session.user,
        UpdateNotificationsRequest(weekly_papers_review_email=True),
    )
    appearance = service.update_user_appearance(
        session.user,
        UpdateAppearanceRequest(theme="dark"),
    )
    github = service.save_github_connection(
        session.user,
        github_user_id="42",
        github_login="octocat",
        access_token="github-access-token",
        scope="repo,read:user",
    )

    assert anthropic.has_anthropic_api_key is True
    assert anthropic.settings.anthropic_api_key_hint.startswith("anth")
    assert modal_credentials.settings.has_modal_credentials is True
    assert modal_credentials.settings.modal_token_id_hint.startswith("moda")
    assert notifications.settings.weekly_papers_review_email is True
    assert appearance.settings.appearance_theme == "dark"
    assert github.settings.has_github_connection is True
    assert github.settings.github_login == "octocat"

    cleared = service.delete_user_modal_credentials(session.user)
    disconnected = service.delete_github_connection(session.user)

    assert cleared.settings.has_modal_credentials is False
    assert disconnected.settings.has_github_connection is False


def test_invalid_saved_anthropic_key_is_cleared_and_gates_chat(browser_service):
    service, _, _ = browser_service
    session, _ = service.register_browser_user(
        SignupRequest(email="stale-key@example.com", password="topsecret123", display_name="Stale Key User")
    )
    assert session.user is not None

    service.save_user_anthropic_key(
        session.user,
        SaveAnthropicKeyRequest(api_key="anthropic-user-key-999"),
    )
    service.repository.save_anthropic_api_key(  # type: ignore[attr-defined]
        user_id=session.user.user_id,
        encrypted_api_key="stale-invalid-token",
        hint="anth...999",
    )
    for _ in range(service.settings.free_chat_response_limit):
        service.repository.increment_free_responses_used(session.user.user_id)  # type: ignore[attr-defined]

    thread = service.create_chat_thread(
        session.user,
        CreateChatThreadRequest(title="New chat"),
    )

    response = service.send_chat_message(
        session.user,
        thread.thread_id,
        SendChatMessageRequest(content="Please continue", tag_filters=[]),
    )
    service.repository.save_anthropic_api_key(  # type: ignore[attr-defined]
        user_id=session.user.user_id,
        encrypted_api_key="stale-invalid-token",
        hint="anth...999",
    )
    streamed = list(
        service.stream_chat_message(
            session.user,
            thread.thread_id,
            SendChatMessageRequest(content="Please continue again", tag_filters=[]),
        )
    )

    assert response.state == "api_key_invalid"
    assert response.has_anthropic_api_key is False
    assert service.repository.get_anthropic_api_key_record(session.user.user_id) is None  # type: ignore[attr-defined]
    assert [event_name for event_name, _ in streamed] == ["api_key_required"]
    assert streamed[0][1]["state"] == "api_key_invalid"


def test_sanitize_inline_citations_preserves_markdown_structure():
    answer = """# Findings

- Keep this citation [S1]
- Remove this one [S9]

```python
if grounded:
    print("still indented")
```
"""

    sanitized = _sanitize_inline_citations(
        answer,
        [
            ChatCitation(
                source_id="S1",
                source_kind="paperbase_paper",
                title="Grounding Paper",
                path="sections/findings.md",
                excerpt="Grounded finding.",
            )
        ],
    )

    assert sanitized == """# Findings

- Keep this citation [S1]
- Remove this one 

```python
if grounded:
    print("still indented")
```"""


def test_python_file_creation_requires_github_and_normalizes_names(browser_service):
    service, _, _ = browser_service
    session, _ = service.register_browser_user(
        SignupRequest(email="python@example.com", password="topsecret123", display_name="Python User")
    )
    assert session.user is not None

    project_id = service.list_projects(session.user).projects[0].project_id

    with pytest.raises(HTTPException) as excinfo:
        service.create_python_file(session.user, project_id)
    assert excinfo.value.status_code == 409

    service.save_github_connection(
        session.user,
        github_user_id="42",
        github_login="octocat",
        access_token="github-access-token",
        scope="repo,read:user",
    )

    first = service.create_python_file(session.user, project_id)
    second = service.create_python_file(session.user, project_id)
    renamed = service.update_python_file(
        session.user,
        first.file_id,
        filename="analysis",
        content="print('hello')",
    )

    assert first.filename == "something.py"
    assert second.filename == "something_2.py"
    assert renamed.filename == "analysis.py"
    assert renamed.content == "print('hello')"


def test_python_file_sync_project_repo_writes_metadata_and_files():
    repo_manager = FakeGitHubRepoManager()
    service = PaperbaseService(
        settings=Settings(
            storage_backend="memory",
            database_url="memory://",
            api_key="test-token",
            anthropic_api_key="product-key-123",
        ),
        repository=MemoryPaperRepository(),
        object_store=MemoryObjectStore(),
        github_repo_manager=repo_manager,
    )

    session, _ = service.register_browser_user(
        SignupRequest(email="sync@example.com", password="topsecret123", display_name="Sync User")
    )
    assert session.user is not None
    service.save_github_connection(
        session.user,
        github_user_id="42",
        github_login="octocat",
        access_token="github-access-token",
        scope="repo,read:user",
    )

    project = service.list_projects(session.user).projects[0]
    python_file = service.create_python_file(session.user, project.project_id)
    service.update_python_file(
        session.user,
        python_file.file_id,
        filename="analysis.py",
        content="print('sync')",
    )

    synced = service.sync_project_repo(
        session.user,
        project.project_id,
        ProjectRepoSyncRequest(commit_message="Save analysis.py"),
    )

    assert synced.repo_exists is True
    assert synced.github_repo_name == "paperbase-general-123abc"
    assert synced.last_sync_commit_sha == "abc123"
    assert len(repo_manager.ensure_repo_calls) == 1
    assert len(repo_manager.sync_project_files_calls) == 1
    synced_files = repo_manager.sync_project_files_calls[0]["files"]
    assert synced_files["src/analysis.py"] == "print('sync')"
    assert "notes/.gitkeep" in synced_files
    assert ".paperbase" in synced_files
    manifest = json.loads(synced_files[".paperbase"])
    assert manifest["project_id"] == project.project_id
    assert manifest["project_name"] == project.name
    assert manifest["custom_instructions"] == project.description
    assert manifest["paths"] == {"src": "src", "notes": "notes", "metadata": ".paperbase"}
    assert repo_manager.sync_project_files_calls[0]["base_commit_sha"] is None
    assert repo_manager.sync_project_files_calls[0]["expected_remote_head_sha"] is None


def test_sync_project_repo_applies_conflict_resolutions_before_retry():
    repo_manager = FakeGitHubRepoManager()
    service = PaperbaseService(
        settings=Settings(
            storage_backend="memory",
            database_url="memory://",
            api_key="test-token",
            anthropic_api_key="product-key-123",
        ),
        repository=MemoryPaperRepository(),
        object_store=MemoryObjectStore(),
        github_repo_manager=repo_manager,
    )

    session, _ = service.register_browser_user(
        SignupRequest(email="resolve@example.com", password="topsecret123", display_name="Resolve User")
    )
    assert session.user is not None
    service.save_github_connection(
        session.user,
        github_user_id="42",
        github_login="octocat",
        access_token="github-access-token",
        scope="repo,read:user",
    )

    project = service.list_projects(session.user).projects[0]
    python_file = service.create_python_file(session.user, project.project_id)
    updated_file = service.update_python_file(
        session.user,
        python_file.file_id,
        filename="analysis.py",
        content="print('local')",
    )
    service.repository.save_project_repo_sync(  # type: ignore[attr-defined]
        user_id=session.user.user_id,
        project_id=project.project_id,
        github_repo_name="paperbase-general-123abc",
        github_repo_url="https://github.com/octocat/paperbase-general-123abc",
        default_branch="main",
        last_sync_commit_sha="abc123",
        last_sync_commit_url="https://github.com/octocat/paperbase-general-123abc/commit/abc123",
        last_synced_at=datetime(2026, 4, 21, tzinfo=timezone.utc),
    )

    synced = service.sync_project_repo(
        session.user,
        project.project_id,
        ProjectRepoSyncRequest(
            commit_message="Resolve analysis.py",
            expected_remote_head_sha="def456",
            conflict_resolutions=[
                {
                    "path": "src/analysis.py",
                    "content": "print('merged')",
                }
            ],
        ),
    )

    assert synced.last_sync_commit_sha == "abc123"
    assert service.get_python_file(session.user, updated_file.file_id).content == "print('merged')"
    assert repo_manager.sync_project_files_calls[0]["files"]["src/analysis.py"] == "print('merged')"
    assert repo_manager.sync_project_files_calls[0]["base_commit_sha"] == "abc123"
    assert repo_manager.sync_project_files_calls[0]["expected_remote_head_sha"] == "def456"


def test_sync_project_repo_surfaces_merge_conflicts():
    repo_manager = FakeGitHubRepoManager()
    repo_manager.next_sync_conflict = GitHubSyncConflictError(
        "The project repository changed on GitHub and needs conflict resolution.",
        repo_name="paperbase-general-123abc",
        repo_url="https://github.com/octocat/paperbase-general-123abc",
        default_branch="main",
        base_commit_sha="abc123",
        remote_head_sha="def456",
        conflicts=[
            GitHubConflictFile(
                path="src/analysis.py",
                base_content="print('base')",
                local_content="print('local')",
                remote_content="print('remote')",
            )
        ],
    )
    service = PaperbaseService(
        settings=Settings(
            storage_backend="memory",
            database_url="memory://",
            api_key="test-token",
            anthropic_api_key="product-key-123",
        ),
        repository=MemoryPaperRepository(),
        object_store=MemoryObjectStore(),
        github_repo_manager=repo_manager,
    )

    session, _ = service.register_browser_user(
        SignupRequest(email="conflict@example.com", password="topsecret123", display_name="Conflict User")
    )
    assert session.user is not None
    service.save_github_connection(
        session.user,
        github_user_id="42",
        github_login="octocat",
        access_token="github-access-token",
        scope="repo,read:user",
    )

    project = service.list_projects(session.user).projects[0]
    python_file = service.create_python_file(session.user, project.project_id)
    service.update_python_file(
        session.user,
        python_file.file_id,
        filename="analysis.py",
        content="print('local')",
    )
    service.repository.save_project_repo_sync(  # type: ignore[attr-defined]
        user_id=session.user.user_id,
        project_id=project.project_id,
        github_repo_name="paperbase-general-123abc",
        github_repo_url="https://github.com/octocat/paperbase-general-123abc",
        default_branch="main",
        last_sync_commit_sha="abc123",
        last_sync_commit_url="https://github.com/octocat/paperbase-general-123abc/commit/abc123",
        last_synced_at=datetime(2026, 4, 21, tzinfo=timezone.utc),
    )

    with pytest.raises(HTTPException) as exc_info:
        service.sync_project_repo(
            session.user,
            project.project_id,
            ProjectRepoSyncRequest(commit_message="Save analysis.py"),
        )

    assert exc_info.value.status_code == 409
    detail = exc_info.value.detail
    assert detail["code"] == "github_merge_conflict"
    assert detail["remote_head_sha"] == "def456"
    assert detail["conflicts"][0]["path"] == "src/analysis.py"


def test_run_python_file_requires_modal_credentials_and_uses_saved_content():
    modal_runner = FakeModalRunner()
    service = PaperbaseService(
        settings=Settings(
            storage_backend="memory",
            database_url="memory://",
            api_key="test-token",
            anthropic_api_key="product-key-123",
        ),
        repository=MemoryPaperRepository(),
        object_store=MemoryObjectStore(),
        modal_runner=modal_runner,
    )

    session, _ = service.register_browser_user(
        SignupRequest(email="run@example.com", password="topsecret123", display_name="Run User")
    )
    assert session.user is not None
    service.save_github_connection(
        session.user,
        github_user_id="42",
        github_login="octocat",
        access_token="github-access-token",
        scope="repo,read:user",
    )

    project_id = service.list_projects(session.user).projects[0].project_id
    python_file = service.create_python_file(session.user, project_id)
    service.update_python_file(
        session.user,
        python_file.file_id,
        filename=None,
        content="print('run')",
    )

    with pytest.raises(HTTPException) as excinfo:
        service.run_python_file(session.user, python_file.file_id)
    assert excinfo.value.status_code == 409

    service.save_user_modal_credentials(
        session.user,
        SaveModalCredentialsRequest(
            token_id="modal-token-id-999",
            token_secret="modal-token-secret-999",
        ),
    )
    result = service.run_python_file(session.user, python_file.file_id)

    assert result.status == "completed"
    assert result.stdout == "runner output"
    assert modal_runner.calls == [
        {
            "token_id": "modal-token-id-999",
            "token_secret": "modal-token-secret-999",
            "code": "print('run')",
        }
    ]


def test_browser_uploads_and_chat_require_api_key_after_free_quota(monkeypatch, browser_service):
    service, _, chat_model = browser_service
    app.dependency_overrides[get_service] = lambda: service
    client = TestClient(app)

    signup = client.post(
        "/v1/browser/auth/signup",
        json={"email": "chat@example.com", "password": "topsecret123", "display_name": "Chat User"},
    )
    assert signup.status_code == 200
    csrf_headers = {"X-Paperbase-CSRF": signup.json()["csrf_token"]}

    monkeypatch.setattr("paperbase.service.sources.enrich_with_semantic_scholar", lambda papers: papers)
    monkeypatch.setattr(
        "paperbase.service.index_paper_artifacts",
        lambda paper: _rich_extraction(text="Methods section explains grounding and citations."),
    )
    service.index_arxiv_paper(
        "2401.77777",
        paper=_sample_paper("2401.77777", title="Grounding Paper", abstract="A paper about grounded citations"),
    )

    upload = client.post(
        "/v1/browser/uploads",
        data={"tags": "methods,notes"},
        files={
            "file": ("notes.md", b"# Notes\n\nThese uploaded notes discuss methods and grounding.", "text/markdown")
        },
        headers=csrf_headers,
    )
    assert upload.status_code == 200
    assert upload.json()["tags"] == ["methods", "notes"]

    thread = client.post("/v1/browser/chats", json={"title": "New chat"}, headers=csrf_headers).json()
    thread_id = thread["thread_id"]

    for _ in range(3):
        response = client.post(
            f"/v1/browser/chats/{thread_id}/messages",
            json={"content": "Explain the methods grounding with citations", "tag_filters": ["methods"]},
            headers=csrf_headers,
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["state"] == "completed"
        assert payload["assistant_message"]["citations"]

    gated = client.post(
        f"/v1/browser/chats/{thread_id}/messages",
        json={"content": "One more answer please", "tag_filters": ["methods"]},
        headers=csrf_headers,
    )
    assert gated.status_code == 200
    assert gated.json()["state"] == "api_key_required"

    save_key = client.put(
        "/v1/browser/settings/anthropic-key", json={"api_key": "anthropic-user-key-999"}, headers=csrf_headers
    )
    assert save_key.status_code == 200
    assert save_key.json()["has_anthropic_api_key"] is True
    csrf_headers = {"X-Paperbase-CSRF": save_key.json()["csrf_token"]}

    resumed = client.post(
        f"/v1/browser/chats/{thread_id}/messages",
        json={"content": "Continue with my saved key", "tag_filters": ["methods"]},
        headers=csrf_headers,
    )
    assert resumed.status_code == 200
    assert resumed.json()["state"] == "completed"
    assert chat_model.create_calls
    assert chat_model.create_calls[0]["tool_choice"] == {"type": "any"}
    assert chat_model.create_calls[0]["tools"]

    app.dependency_overrides.clear()


def test_non_grounded_chat_skips_tools_and_citations(browser_service):
    service, _, chat_model = browser_service
    session, _ = service.register_browser_user(
        SignupRequest(email="casual@example.com", password="topsecret123", display_name="Casual User")
    )
    assert session.user is not None

    thread = service.create_chat_thread(session.user, CreateChatThreadRequest(title="New chat"))
    response = service.send_chat_message(
        session.user,
        thread.thread_id,
        SendChatMessageRequest(content="Help me brainstorm a short product name"),
    )

    assert response.assistant_message is not None
    assert response.assistant_message.citations == []
    assert "[S" not in response.assistant_message.content_markdown
    assert len(chat_model.create_calls) == 1
    assert chat_model.create_calls[0]["tools"] is None
    assert "do not invent source ids" in str(chat_model.create_calls[0]["system_prompt"])


def test_search_only_tool_results_do_not_create_citations(monkeypatch):
    class SearchOnlyChatModel(FakeChatModel):
        def create_message(self, *, api_key: str, messages, system_prompt: str, tools=None, tool_choice=None):
            self.create_calls.append(
                {
                    "api_key": api_key,
                    "messages": messages,
                    "system_prompt": system_prompt,
                    "tools": tools,
                    "tool_choice": tool_choice,
                }
            )
            if tools:
                has_tool_results = any(
                    message.get("role") == "user"
                    and isinstance(message.get("content"), list)
                    and any(block.get("type") == "tool_result" for block in message.get("content"))
                    for message in messages
                    if isinstance(message, dict)
                )
                if tool_choice == {"type": "any"} or not has_tool_results:
                    return {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "toolu_search_only",
                                "name": "paperbase_search",
                                "input": {"query": "grounding", "limit": 3},
                            }
                        ],
                        "stop_reason": "tool_use",
                    }
            return {
                "content": [
                    {
                        "type": "text",
                        "text": "Grounded answer without evidence citations [S1].",
                    }
                ],
                "stop_reason": "end_turn",
            }

    chat_model = SearchOnlyChatModel()
    service = PaperbaseService(
        settings=Settings(
            storage_backend="memory",
            database_url="memory://",
            api_key="test-token",
            anthropic_api_key="product-key-123",
        ),
        repository=MemoryPaperRepository(),
        object_store=MemoryObjectStore(),
        email_sender=FakeEmailSender(),
        chat_model=chat_model,
    )
    session, _ = service.register_browser_user(
        SignupRequest(email="search-only@example.com", password="topsecret123", display_name="Search Only")
    )
    assert session.user is not None

    monkeypatch.setattr("paperbase.service.sources.enrich_with_semantic_scholar", lambda papers: papers)
    monkeypatch.setattr(
        "paperbase.service.index_paper_artifacts",
        lambda paper: _rich_extraction(text="Methods section explains grounding and citations."),
    )
    service.index_arxiv_paper(
        "2403.00001",
        paper=_sample_paper("2403.00001", title="Grounding Paper", abstract="A paper about grounded citations"),
    )

    thread = service.create_chat_thread(session.user, CreateChatThreadRequest(title="New chat"))
    response = service.send_chat_message(
        session.user,
        thread.thread_id,
        SendChatMessageRequest(content="Explain the methods grounding with citations"),
    )

    assert response.assistant_message is not None
    assert response.assistant_message.citations == []
    assert "[S" not in response.assistant_message.content_markdown


def test_link_requests_force_tool_use_and_prompt_for_paper_links(monkeypatch, browser_service):
    service, _, chat_model = browser_service
    session, _ = service.register_browser_user(
        SignupRequest(email="links@example.com", password="topsecret123", display_name="Links User")
    )
    assert session.user is not None

    monkeypatch.setattr("paperbase.service.sources.enrich_with_semantic_scholar", lambda papers: papers)
    monkeypatch.setattr(
        "paperbase.service.index_paper_artifacts",
        lambda paper: _rich_extraction(text="## Methods\n\nGrounding methods and benchmark comparisons."),
    )
    service.index_arxiv_paper(
        "2402.42424",
        paper=_sample_paper("2402.42424", title="Grounding Links Paper", abstract="A paper with useful metadata"),
    )

    thread = service.create_chat_thread(session.user, CreateChatThreadRequest(title="New chat"))
    service.send_chat_message(
        session.user,
        thread.thread_id,
        SendChatMessageRequest(content="Give me links to papers about grounding"),
    )

    assert chat_model.create_calls[0]["tool_choice"] == {"type": "any"}
    assert "html_url or pdf_url" in str(chat_model.create_calls[0]["system_prompt"])
    assert "markdown links to the paper pages or PDFs" in str(chat_model.create_calls[-1]["system_prompt"])


def test_project_defaults_and_thread_snapshots(browser_service):
    service, _, chat_model = browser_service
    session, _ = service.register_browser_user(
        SignupRequest(email="projects@example.com", password="topsecret123", display_name="Project User")
    )
    assert session.user is not None

    initial_projects = service.list_projects(session.user).projects
    assert len(initial_projects) == 1
    assert initial_projects[0].name == "General"
    assert initial_projects[0].is_default is True
    assert initial_projects[0].session_count == 0

    project = service.create_project(
        session.user,
        CreateProjectRequest(name="Agents", description="Use grounded citations only."),
    )
    thread = service.create_chat_thread(
        session.user,
        CreateChatThreadRequest(title="Agent chat", project_id=project.project_id),
    )

    assert thread.project_id == project.project_id
    assert thread.project_name == "Agents"
    assert thread.project_instructions == "Use grounded citations only."

    projects_after_thread = service.list_projects(session.user).projects
    agents_project = next(item for item in projects_after_thread if item.project_id == project.project_id)
    assert agents_project.session_count == 1

    service.update_project(
        session.user,
        project.project_id,
        UpdateProjectRequest(name="Agents", description="Use different instructions now."),
    )

    response = service.send_chat_message(
        session.user,
        thread.thread_id,
        SendChatMessageRequest(content="Help me brainstorm a short product name"),
    )

    assert response.thread.project_instructions == "Use grounded citations only."
    assert chat_model.create_calls
    assert "Use grounded citations only." in str(chat_model.create_calls[-1]["system_prompt"])
    assert "Use different instructions now." not in str(chat_model.create_calls[-1]["system_prompt"])


def test_project_session_paging_and_thread_deletion(browser_service):
    service, _, _ = browser_service
    session, _ = service.register_browser_user(
        SignupRequest(email="paging@example.com", password="topsecret123", display_name="Paging User")
    )
    assert session.user is not None

    project = service.create_project(
        session.user,
        CreateProjectRequest(name="Benchmarks", description="Track benchmark sessions"),
    )
    threads = [
        service.create_chat_thread(
            session.user,
            CreateChatThreadRequest(title=f"Thread {index}", project_id=project.project_id),
        )
        for index in range(6)
    ]

    first_page = service.list_project_chat_threads(session.user, project.project_id, limit=5, offset=0)
    second_page = service.list_project_chat_threads(session.user, project.project_id, limit=5, offset=5)

    assert len(first_page.threads) == 5
    assert first_page.total_count == 6
    assert first_page.has_more is True
    assert all(item.project_id == project.project_id for item in first_page.threads)
    assert len(second_page.threads) == 1
    assert second_page.has_more is False

    service.delete_chat_thread(session.user, threads[0].thread_id)

    remaining_page = service.list_project_chat_threads(session.user, project.project_id, limit=10, offset=0)
    assert remaining_page.total_count == 5
    assert threads[0].thread_id not in {item.thread_id for item in remaining_page.threads}
    remaining_project = next(
        item for item in service.list_projects(session.user).projects if item.project_id == project.project_id
    )
    assert remaining_project.session_count == 5


def test_empty_chat_can_move_between_projects(browser_service):
    service, _, _ = browser_service
    session, _ = service.register_browser_user(
        SignupRequest(email="move-empty@example.com", password="topsecret123", display_name="Move Empty")
    )
    assert session.user is not None

    project = service.create_project(
        session.user,
        CreateProjectRequest(name="Agents", description="Study agent systems"),
    )
    thread = service.create_chat_thread(session.user, CreateChatThreadRequest(title="New chat"))

    moved = service.move_chat_thread_to_project(
        session.user,
        thread.thread_id,
        MoveChatThreadProjectRequest(project_id=project.project_id),
    )

    assert moved.project_id == project.project_id
    assert moved.project_name == "Agents"
    assert moved.project_instructions == "Study agent systems"

    service.send_chat_message(
        session.user,
        moved.thread_id,
        SendChatMessageRequest(content="Help me brainstorm a short product name"),
    )
    with pytest.raises(HTTPException) as exc:
        service.move_chat_thread_to_project(
            session.user,
            moved.thread_id,
            MoveChatThreadProjectRequest(project_id=project.project_id),
        )
    assert exc.value.status_code == 409


def test_delete_project_cascades_threads_and_protects_default(browser_service):
    service, _, _ = browser_service
    session, _ = service.register_browser_user(
        SignupRequest(email="cascade@example.com", password="topsecret123", display_name="Cascade User")
    )
    assert session.user is not None

    default_project = service.list_projects(session.user).projects[0]
    with pytest.raises(HTTPException) as default_exc:
        service.delete_project(session.user, default_project.project_id)
    assert default_exc.value.status_code == 400

    project = service.create_project(
        session.user,
        CreateProjectRequest(name="Archive", description="Delete me"),
    )
    thread = service.create_chat_thread(
        session.user,
        CreateChatThreadRequest(title="Disposable thread", project_id=project.project_id),
    )

    service.delete_project(session.user, project.project_id)

    remaining_project_ids = {item.project_id for item in service.list_projects(session.user).projects}
    assert project.project_id not in remaining_project_ids
    with pytest.raises(HTTPException) as thread_exc:
        service.get_chat_thread(session.user, thread.thread_id)
    assert thread_exc.value.status_code == 404


def test_browser_chat_stream_emits_citations_and_completion(monkeypatch, browser_service):
    service, _, _ = browser_service
    app.dependency_overrides[get_service] = lambda: service
    client = TestClient(app)

    signup = client.post(
        "/v1/browser/auth/signup",
        json={"email": "stream@example.com", "password": "topsecret123", "display_name": "Stream User"},
    )
    assert signup.status_code == 200
    csrf_headers = {"X-Paperbase-CSRF": signup.json()["csrf_token"]}

    monkeypatch.setattr("paperbase.service.sources.enrich_with_semantic_scholar", lambda papers: papers)
    monkeypatch.setattr(
        "paperbase.service.index_paper_artifacts",
        lambda paper: _rich_extraction(text="## Methods\n\nMethods section explains grounding and citations."),
    )
    service.index_arxiv_paper(
        "2401.88888",
        paper=_sample_paper("2401.88888", title="Stream Grounding Paper", abstract="A paper about streaming citations"),
    )

    thread = client.post("/v1/browser/chats", json={"title": "New chat"}, headers=csrf_headers).json()
    thread_id = thread["thread_id"]

    with client.stream(
        "POST",
        f"/v1/browser/chats/{thread_id}/messages/stream",
        json={"content": "Explain the methods grounding with citations", "tag_filters": []},
        headers=csrf_headers,
    ) as response:
        assert response.status_code == 200
        events = _parse_sse_events("".join(response.iter_text()))

    event_names = [event_name for event_name, _ in events]
    assert event_names[:2] == ["tool_start", "tool_done"]
    assert events[0][1]["label"]
    assert "assistant_start" in event_names
    assert "delta" in event_names
    assert event_names[-1] == "done"
    assistant_start_index = event_names.index("assistant_start")
    assert events[assistant_start_index][1]["assistant_message"]["citations"]
    assert events[-1][1]["assistant_message"]["status"] == "completed"

    refreshed = client.get(f"/v1/browser/chats/{thread_id}").json()
    assert refreshed["messages"][-1]["status"] == "completed"
    assert refreshed["messages"][-1]["citations"]

    app.dependency_overrides.clear()


def test_browser_chat_stream_handles_api_key_required_without_placeholder(browser_service):
    service, _, _ = browser_service
    app.dependency_overrides[get_service] = lambda: service
    client = TestClient(app)

    signup = client.post(
        "/v1/browser/auth/signup",
        json={"email": "stream-gated@example.com", "password": "topsecret123", "display_name": "Gated User"},
    )
    assert signup.status_code == 200
    payload = signup.json()
    csrf_headers = {"X-Paperbase-CSRF": payload["csrf_token"]}
    user_id = payload["user"]["user_id"]

    for _ in range(3):
        service.repository.increment_free_responses_used(user_id)  # type: ignore[attr-defined]

    thread = client.post("/v1/browser/chats", json={"title": "New chat"}, headers=csrf_headers).json()
    thread_id = thread["thread_id"]

    with client.stream(
        "POST",
        f"/v1/browser/chats/{thread_id}/messages/stream",
        json={"content": "One more answer please", "tag_filters": []},
        headers=csrf_headers,
    ) as response:
        assert response.status_code == 200
        events = _parse_sse_events("".join(response.iter_text()))

    assert [event_name for event_name, _ in events] == ["api_key_required"]
    assert events[0][1]["state"] == "api_key_required"
    assert events[0][1]["assistant_message"] is None
    assert [message["role"] for message in events[0][1]["thread"]["messages"]] == ["user"]

    app.dependency_overrides.clear()


def test_browser_chat_stream_marks_partial_failure(monkeypatch, browser_service):
    service, _, chat_model = browser_service
    app.dependency_overrides[get_service] = lambda: service
    client = TestClient(app)

    signup = client.post(
        "/v1/browser/auth/signup",
        json={"email": "stream-fail@example.com", "password": "topsecret123", "display_name": "Fail User"},
    )
    assert signup.status_code == 200
    csrf_headers = {"X-Paperbase-CSRF": signup.json()["csrf_token"]}

    monkeypatch.setattr("paperbase.service.sources.enrich_with_semantic_scholar", lambda papers: papers)
    monkeypatch.setattr(
        "paperbase.service.index_paper_artifacts",
        lambda paper: _rich_extraction(text="Methods section explains grounding and citations."),
    )
    service.index_arxiv_paper(
        "2401.99999",
        paper=_sample_paper("2401.99999", title="Failure Paper", abstract="A paper about stream failure"),
    )
    chat_model.fail_stream_after_chunks = 1

    thread = client.post("/v1/browser/chats", json={"title": "New chat"}, headers=csrf_headers).json()
    thread_id = thread["thread_id"]

    with client.stream(
        "POST",
        f"/v1/browser/chats/{thread_id}/messages/stream",
        json={"content": "Explain the methods grounding with citations", "tag_filters": []},
        headers=csrf_headers,
    ) as response:
        assert response.status_code == 200
        events = _parse_sse_events("".join(response.iter_text()))

    event_names = [event_name for event_name, _ in events]
    assert event_names[:3] == ["tool_start", "tool_done", "assistant_start"]
    assert "delta" in event_names
    assert events[-1][0] == "error"
    refreshed = client.get(f"/v1/browser/chats/{thread_id}").json()
    assert refreshed["messages"][-1]["status"] == "failed"
    assert refreshed["messages"][-1]["content_markdown"] == "Grounded"

    app.dependency_overrides.clear()


def test_browser_paper_read_endpoints(monkeypatch, browser_service):
    service, _, _ = browser_service
    app.dependency_overrides[get_service] = lambda: service
    client = TestClient(app)

    signup = client.post(
        "/v1/browser/auth/signup",
        json={"email": "papers@example.com", "password": "topsecret123", "display_name": "Paper User"},
    )
    assert signup.status_code == 200

    monkeypatch.setattr("paperbase.service.sources.enrich_with_semantic_scholar", lambda papers: papers)
    monkeypatch.setattr(
        "paperbase.service.index_paper_artifacts",
        lambda paper: _rich_extraction(text="## Introduction\n\nGrounded section content."),
    )
    indexed = service.index_arxiv_paper(
        "2402.00001",
        paper=_sample_paper("2402.00001", title="Drawer Paper", abstract="A paper for the drawer"),
    )

    paper = client.get(f"/v1/browser/papers/{indexed.paper_id}")
    assert paper.status_code == 200
    assert paper.json()["title"] == "Drawer Paper"

    file_response = client.get(f"/v1/browser/papers/{indexed.paper_id}/files/sections/introduction.md")
    assert file_response.status_code == 200
    assert "Grounded section content." in file_response.json()["content"]

    figure = client.get(f"/v1/browser/papers/{indexed.paper_id}/figures/figure-1")
    assert figure.status_code == 200
    assert figure.json()["caption"] == "A model architecture."

    table = client.get(f"/v1/browser/papers/{indexed.paper_id}/tables/table-1")
    assert table.status_code == 200
    assert table.json()["rows"][1] == ["Paperbase", "1.0"]

    app.dependency_overrides.clear()


def test_browser_write_endpoints_require_csrf(browser_service):
    service, _, _ = browser_service
    app.dependency_overrides[get_service] = lambda: service
    client = TestClient(app)

    signup = client.post(
        "/v1/browser/auth/signup",
        json={"email": "csrf@example.com", "password": "topsecret123", "display_name": "CSRF User"},
    )
    assert signup.status_code == 200
    assert signup.json()["csrf_token"]

    missing_csrf = client.post("/v1/browser/chats", json={"title": "No token"})
    assert missing_csrf.status_code == 403
    assert missing_csrf.json()["detail"] == "Invalid CSRF token"

    wrong_origin = client.post(
        "/v1/browser/chats",
        json={"title": "Wrong origin"},
        headers={"X-Paperbase-CSRF": signup.json()["csrf_token"], "Origin": "https://evil.example"},
    )
    assert wrong_origin.status_code == 403
    assert wrong_origin.json()["detail"] == "Cross-site browser requests are not allowed"

    app.dependency_overrides.clear()


def test_browser_google_start_redirects_back_to_return_to_when_not_configured(monkeypatch, service: PaperbaseService):
    app.dependency_overrides[get_service] = lambda: service
    client = TestClient(app)

    monkeypatch.setattr("paperbase.api.get_google_oauth_client", lambda: None)

    response = client.get(
        "/v1/browser/auth/google/start",
        params={"return_to": "http://127.0.0.1:8080"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "http://127.0.0.1:8080/?auth_error=google_not_configured"

    app.dependency_overrides.clear()


def test_chat_search_tool_threads_rerank_flag_into_search_request():
    """The paperbase_search tool must advertise a `rerank` input and pass it
    through to SearchRequest so chat/MCP flows can use the deep-search path."""
    from paperbase.service import ChatCitationRegistry

    service = PaperbaseService(
        settings=Settings(storage_backend="memory"),
        repository=MemoryPaperRepository(),
        object_store=MemoryObjectStore(),
    )
    service.init()

    request = SendChatMessageRequest(content="find papers about attention", include_papers=True)
    tools = service._chat_tool_definitions(request)
    search_tool = next((t for t in tools if t["name"] == "paperbase_search"), None)
    assert search_tool is not None, "paperbase_search tool should be defined"
    assert "rerank" in search_tool["input_schema"]["properties"], (
        "paperbase_search tool schema must advertise the rerank input"
    )

    captured: list[bool] = []

    class RecordingService(type(service)):
        pass

    original_search = service.search

    def recording_search(req):
        captured.append(req.rerank)
        return original_search(req)

    service.search = recording_search  # type: ignore[method-assign]

    registry = ChatCitationRegistry()
    fake_user = type("U", (), {"user_id": "u1"})()

    service._execute_chat_tool(
        tool_name="paperbase_search",
        tool_input={"query": "attention", "rerank": True},
        user=fake_user,
        citation_registry=registry,
    )
    assert captured == [True], "rerank=True from tool_input did not reach SearchRequest"

    service._execute_chat_tool(
        tool_name="paperbase_search",
        tool_input={"query": "attention"},
        user=fake_user,
        citation_registry=registry,
    )
    assert captured == [True, False], "rerank default must be False when tool_input omits it"


def test_memory_repository_search_local_bakes_in_citation_and_recency_boosts():
    """MemoryPaperRepository.search_local must apply citation + recency boosts
    into the returned score, mirroring SqlAlchemyPaperRepository. service.search
    skips re-applying citation_boost to local hits, so memory mode would lose
    the boost entirely without this."""
    repo = MemoryPaperRepository()
    matched_query = "shared rare niche topic"

    def _meta(arxiv_id: str, *, cites: int, year: str) -> PaperMetadata:
        return PaperMetadata(
            arxiv_id=arxiv_id,
            title=f"Paper about {matched_query}",
            authors="A",
            abstract="abstract text",
            categories="cs.AI",
            primary_category="cs.AI",
            published=year,
            updated=year,
            pdf_url=f"https://example.test/{arxiv_id}.pdf",
            html_url=f"https://example.test/{arxiv_id}",
            citation_count=cites,
            index_status="indexed",
        )

    repo.upsert_paper(_meta("low/0001", cites=0, year="2018-01-01"), search_document="", embedding=None)
    repo.upsert_paper(_meta("high/0001", cites=10_000, year="2018-01-01"), search_document="", embedding=None)

    no_boost = repo.search_local(
        matched_query, limit=5, query_embedding=None, citation_boost_max=0.0, recency_boost_max=0.0
    )
    with_boost = repo.search_local(matched_query, limit=5, query_embedding=None)

    no_boost_by_id = {r.arxiv_id: r.score or 0 for r in no_boost}
    with_boost_by_id = {r.arxiv_id: r.score or 0 for r in with_boost}

    # With boosts disabled, both papers have the same lexical-only score.
    assert no_boost_by_id["low/0001"] == no_boost_by_id["high/0001"]
    # With boosts enabled, the highly-cited paper picks up additional score.
    assert with_boost_by_id["high/0001"] > with_boost_by_id["low/0001"]
    assert with_boost_by_id["high/0001"] > no_boost_by_id["high/0001"]


def test_query_prefix_for_model_matches_bge_e5_and_leaves_others_unchanged():
    """Retrieval models like BGE / E5 need a model-specific prefix prepended
    to the query (but not the document) at encode time. SPECTER2 and generic
    models get no prefix. Regressing this silently loses retrieval recall."""
    from paperbase.service import _query_prefix_for_model

    assert _query_prefix_for_model("BAAI/bge-base-en-v1.5") == (
        "Represent this sentence for searching relevant passages: "
    )
    assert _query_prefix_for_model("BAAI/bge-large-en-v1.5") == (
        "Represent this sentence for searching relevant passages: "
    )
    assert _query_prefix_for_model("intfloat/e5-large-v2") == "query: "
    assert _query_prefix_for_model("intfloat/multilingual-e5-large") == "query: "
    assert _query_prefix_for_model("allenai/specter2_base") == ""
    assert _query_prefix_for_model("sentence-transformers/all-MiniLM-L6-v2") == ""
    assert _query_prefix_for_model(None) == ""
    assert _query_prefix_for_model("") == ""


def test_encode_prepends_query_prefix_for_bge_but_not_for_specter2(monkeypatch):
    """The model-aware prefix must be injected in encode() (query side) and
    must not affect encode_batch() callers (document side) or encode_paper*."""
    from paperbase.config import Settings
    from paperbase.service import OptionalEmbeddingProvider

    import numpy as np

    class _FakeModel:
        def __init__(self):
            self.calls: list[list[str]] = []

        def encode(self, texts, **_):
            # Record what actually reached the underlying model, so we can
            # verify the prefix arrived (or did not) exactly as intended.
            self.calls.append(list(texts))
            return np.zeros((len(texts), 768), dtype="float32")

    # BGE-base -> prefix applied in encode(), not in encode_batch()
    bge_settings = Settings(
        storage_backend="memory", database_url="memory://", api_key="t",
        summary_provider="none",
        embedding_model="BAAI/bge-base-en-v1.5", embedding_dim=768,
    )
    bge_provider = OptionalEmbeddingProvider(bge_settings)
    bge_model = _FakeModel()
    monkeypatch.setattr(bge_provider, "_model", lambda: bge_model)

    bge_provider.encode("vision transformer")
    assert bge_model.calls[-1] == [
        "Represent this sentence for searching relevant passages: vision transformer"
    ]

    bge_provider.encode_batch(["doc one", "doc two"])
    assert bge_model.calls[-1] == ["doc one", "doc two"], (
        "encode_batch is the document-side entry point and must not apply the query prefix"
    )

    # SPECTER2 -> no prefix
    specter_settings = Settings(
        storage_backend="memory", database_url="memory://", api_key="t",
        summary_provider="none",
        embedding_model="allenai/specter2_base", embedding_dim=768,
    )
    specter_provider = OptionalEmbeddingProvider(specter_settings)
    specter_model = _FakeModel()
    monkeypatch.setattr(specter_provider, "_model", lambda: specter_model)
    specter_provider.encode("vision transformer")
    assert specter_model.calls[-1] == ["vision transformer"]


def _make_service_with_one_paper() -> tuple[PaperbaseService, str]:
    service = PaperbaseService(
        settings=Settings(storage_backend="memory", database_url="memory://", api_key="t"),
        repository=MemoryPaperRepository(),
        object_store=MemoryObjectStore(),
    )
    service.init()
    meta = PaperMetadata(
        arxiv_id="2401.99999",
        title="Logging Test Paper",
        authors="Z",
        abstract="An abstract mentioning attention.",
        categories="cs.AI",
        primary_category="cs.AI",
        published="2024-01-01",
        updated="2024-01-01",
        pdf_url="p",
        html_url="h",
        citation_count=5,
        index_status="indexed",
    )
    paper = service.repository.upsert_paper(meta, search_document="attention", embedding=None)
    return service, paper.paper_id


def test_search_records_query_log_and_returns_query_id_by_default():
    """A default SearchRequest (log_query omitted) persists a row in
    search_logs and returns the row's query_id so the client can follow
    up with a click event."""
    service, paper_id = _make_service_with_one_paper()

    response = service.search(SearchRequest(query="attention", limit=5, include_live_sources=False))

    assert response.query_id is not None
    assert response.query_id.startswith("qry_")
    log = service.repository.search_logs[response.query_id]
    assert log["query_text"] == "attention"
    assert log["request_rerank"] is False
    assert log["top_paper_ids"] == [paper_id]
    assert log["result_set_id"] == response.result_set_id
    assert log["latency_ms"] is not None and log["latency_ms"] >= 0
    assert log["clicked_paper_id"] is None
    assert log["user_id"] is None


def test_search_skips_logging_when_log_query_false():
    """log_query=False is the opt-out path. No row is written and the
    response has query_id=None so clients know click tracking is off."""
    service, _ = _make_service_with_one_paper()

    response = service.search(
        SearchRequest(query="attention", limit=5, include_live_sources=False, log_query=False)
    )

    assert response.query_id is None
    assert service.repository.search_logs == {}


def test_search_attaches_user_id_when_service_caller_provides_one():
    """Callers that know the authenticated user can pass user_id so
    downstream analysis can group queries by user without the search layer
    needing to parse session cookies."""
    service, _ = _make_service_with_one_paper()

    response = service.search(
        SearchRequest(query="attention", limit=5, include_live_sources=False),
        user_id="usr_abc123",
    )

    assert response.query_id is not None
    assert service.repository.search_logs[response.query_id]["user_id"] == "usr_abc123"


def test_search_logging_failure_does_not_break_response(monkeypatch):
    """A storage-side failure in record_search_log must not fail the
    search — ranking quality matters more than observability in the hot
    path. query_id is nulled so the client knows click tracking is
    unavailable."""
    service, paper_id = _make_service_with_one_paper()

    def boom(**_kwargs):
        raise RuntimeError("db went away")

    monkeypatch.setattr(service.repository, "record_search_log", boom)

    response = service.search(SearchRequest(query="attention", limit=5, include_live_sources=False))

    assert [p.paper_id for p in response.papers] == [paper_id]
    assert response.query_id is None


def test_record_search_click_updates_log_and_404s_on_unknown_query_id():
    """The click endpoint is idempotent on the last click. Unknown query_id
    returns False so the API layer can translate to 404."""
    service, paper_id = _make_service_with_one_paper()
    response = service.search(SearchRequest(query="attention", limit=5, include_live_sources=False))
    assert response.query_id is not None

    assert service.record_search_click(response.query_id, paper_id) is True
    log = service.repository.search_logs[response.query_id]
    assert log["clicked_paper_id"] == paper_id
    assert log["clicked_at"] is not None

    assert service.record_search_click("qry_nonexistent", paper_id) is False


def test_search_log_does_not_persist_auth_tokens_or_secrets():
    """Belt-and-braces: even if a caller somehow passes a bearer token or
    API key as the query text, the SearchRequest schema has no field for
    auth material, so nothing but `query` lands in the log. Lock the
    persisted row's keys in place so a future migration can't silently
    add a token-bearing column."""
    service, _ = _make_service_with_one_paper()
    response = service.search(SearchRequest(query="attention", limit=5, include_live_sources=False))
    assert response.query_id is not None

    persisted = service.repository.search_logs[response.query_id]
    assert set(persisted.keys()) == {
        "query_id",
        "user_id",
        "query_text",
        "request_rerank",
        "top_paper_ids",
        "result_set_id",
        "latency_ms",
        "clicked_paper_id",
        "clicked_at",
        "created_at",
    }
    for forbidden in ("api_key", "token", "authorization", "bearer", "secret"):
        assert forbidden not in persisted, f"{forbidden!r} must never appear in search_logs"
