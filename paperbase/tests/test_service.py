from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from paperbase.api import _resolve_bind_address, app
from paperbase.config import Settings, get_settings
from paperbase.extractors import ExtractionOutput, FigureArtifact, RawArtifact, SectionArtifact, TableArtifact
from paperbase.models import (
    CreateChatThreadRequest,
    CreateProjectRequest,
    PaperMetadata,
    SaveAnthropicKeyRequest,
    SaveDaytonaKeyRequest,
    SearchRequest,
    SearchSectionRequest,
    SendChatMessageRequest,
    SignupRequest,
    UpdateNotificationsRequest,
    UpdateProjectRequest,
)
from paperbase.repository import MemoryPaperRepository
from paperbase.service import PaperbaseService, get_service
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
                    token.strip(".,!?():;").lower()
                    for token in raw_query.split()
                    if len(token.strip(".,!?():;")) > 3
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
    monkeypatch.setattr("paperbase.service.index_paper_artifacts", lambda paper: _rich_extraction(text="Attention layers"))

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

    monkeypatch.setattr("paperbase.service.index_paper_artifacts", lambda paper: _full_text_extraction(text="Old extraction"))
    service.index_arxiv_paper(paper.arxiv_id, paper=paper, force=True)

    monkeypatch.setattr("paperbase.service.index_paper_artifacts", lambda paper: _rich_extraction(text="New extraction"))
    service.index_arxiv_paper(paper.arxiv_id, paper=paper, force=True)

    files = {item.path for item in service.list_files("arxiv:2401.33333").files}

    assert "sections/introduction.md" in files
    assert "sections/full-text.md" not in files


def test_search_and_grep_return_result_handles(monkeypatch, service: PaperbaseService):
    monkeypatch.setattr("paperbase.service.sources.enrich_with_semantic_scholar", lambda papers: papers)
    monkeypatch.setattr("paperbase.service.index_paper_artifacts", lambda paper: _rich_extraction(text=(
        "Transformer Study mentions attention and transformers"
        if "Transformer" in paper.title
        else "Vision Study mentions diffusion only"
    )))

    service.index_arxiv_paper("2401.10001", paper=_sample_paper("2401.10001", title="Transformer Study", abstract="Transformers"))
    service.index_arxiv_paper("2401.10002", paper=_sample_paper("2401.10002", title="Vision Study", abstract="Diffusion"))

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
    monkeypatch.setattr("paperbase.service.index_paper_artifacts", lambda paper: _rich_extraction(text="General workflow discussion"))

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

    service.index_arxiv_paper("2401.20001", paper=_sample_paper("2401.20001", title="Tool Use Study", abstract="Planning systems"))
    service.index_arxiv_paper("2401.20002", paper=_sample_paper("2401.20002", title="Image Study", abstract="Vision systems"))

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
    monkeypatch.setattr("paperbase.service.index_paper_artifacts", lambda paper: _rich_extraction(text="Queued attention result"))

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


def _seed_indexed_paper(repo: MemoryPaperRepository, arxiv_id: str, *, with_paper_embedding: bool, chunks_with_embedding: int, chunks_without_embedding: int) -> str:
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
    _seed_indexed_paper(service.repository, "2401.00001", with_paper_embedding=True, chunks_with_embedding=2, chunks_without_embedding=0)
    # Paper B: paper embedding present, chunks missing — the regression case.
    paper_b = _seed_indexed_paper(service.repository, "2401.00002", with_paper_embedding=True, chunks_with_embedding=1, chunks_without_embedding=2)
    # Paper C: paper embedding missing — should also be picked up.
    paper_c = _seed_indexed_paper(service.repository, "2401.00003", with_paper_embedding=False, chunks_with_embedding=0, chunks_without_embedding=1)

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


def test_suggest_and_stats_endpoints(service: PaperbaseService):
    _seed_indexed_paper(service.repository, "2403.00001", with_paper_embedding=True, chunks_with_embedding=2, chunks_without_embedding=0)
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
        paper_id
        for paper_id in paper_ids[10:]
        if not service.repository.list_chunks_missing_embedding(paper_id)
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
        embedder.encode_pairs([
            ("Attention Is All You Need", "We propose a model based solely on attention."),
        ])
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
    assert received == [
        ("Attention Is All You Need", "We propose a model based solely on attention mechanisms.")
    ]
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
    daytona = service.save_user_daytona_key(
        session.user,
        SaveDaytonaKeyRequest(api_key="daytona-user-key-999"),
    )
    notifications = service.update_user_notification_preferences(
        session.user,
        UpdateNotificationsRequest(weekly_papers_review_email=True),
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
    assert daytona.settings.has_daytona_api_key is True
    assert daytona.settings.daytona_api_key_hint.startswith("dayt")
    assert notifications.settings.weekly_papers_review_email is True
    assert github.settings.has_github_connection is True
    assert github.settings.github_login == "octocat"

    cleared = service.delete_user_daytona_key(session.user)
    disconnected = service.delete_github_connection(session.user)

    assert cleared.settings.has_daytona_api_key is False
    assert disconnected.settings.has_github_connection is False


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
        files={"file": ("notes.md", b"# Notes\n\nThese uploaded notes discuss methods and grounding.", "text/markdown")},
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

    save_key = client.put("/v1/browser/settings/anthropic-key", json={"api_key": "anthropic-user-key-999"}, headers=csrf_headers)
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
    assert event_names[0] == "assistant_start"
    assert "delta" in event_names
    assert event_names[-1] == "done"
    assert events[0][1]["assistant_message"]["citations"]
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

    assert [event_name for event_name, _ in events][:2] == ["assistant_start", "delta"]
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
