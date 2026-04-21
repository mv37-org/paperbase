from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from paperbase.api import _resolve_bind_address, app
from paperbase.config import Settings
from paperbase.extractors import ExtractionOutput, FigureArtifact, RawArtifact, SectionArtifact, TableArtifact
from paperbase.models import PaperMetadata, SearchRequest, SearchSectionRequest
from paperbase.repository import MemoryPaperRepository
from paperbase.service import PaperbaseService, get_service
from paperbase.storage import MemoryObjectStore


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
    headers = {"Authorization": "Bearer development-token"}
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

    response = client.post("/v1/debug/integrations", headers={"Authorization": "Bearer development-token"})

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
        self.mode = "paper"

    def encode(self, text: str) -> list[float] | None:
        return [float(len(text)), 0.0, 0.0]

    def encode_batch(self, texts: list[str]) -> list[list[float]] | None:
        if self.mode == "paper":
            self.paper_calls.append(list(texts))
            self.mode = "chunk"
        else:
            self.chunk_calls.append(list(texts))
            self.mode = "paper"
        return [[float(i), 0.0, 0.0] for i, _ in enumerate(texts, start=1)]


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
    assert response[0]["matched_field"] in {"title", "title_prefix", "authors"}

    stats = service.get_stats()
    assert stats["indexed_papers"] >= 1
    assert stats["papers_with_embedding"] >= 1
    assert stats["total_chunks"] >= 2
