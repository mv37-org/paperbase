from __future__ import annotations

from datetime import datetime, timezone

from paperbase.config import Settings
from paperbase.models import PaperMetadata
from paperbase.repository import MemoryPaperRepository
from paperbase.service import PaperbaseService
from paperbase.storage import MemoryObjectStore


def test_browser_trending_detail_exposes_research_metadata() -> None:
    service = PaperbaseService(
        settings=Settings(
            storage_backend="memory",
            database_url="memory://",
            api_key="test-token",
        ),
        repository=MemoryPaperRepository(),
        object_store=MemoryObjectStore(),
    )
    paper = PaperMetadata(
        arxiv_id="2404.22222",
        title="Metadata Rich Paper",
        authors="Ada Lovelace, Grace Hopper",
        abstract="Full abstract for a metadata-rich trending paper.",
        tldr="Semantic Scholar TLDR",
        summary_short="Paperbase summary",
        categories="cs.AI cs.LG",
        primary_category="cs.AI",
        published="2026-04-21",
        updated="2026-04-22",
        doi="10.1234/metadata",
        comment="Accepted at DetailConf",
        journal_ref="Detail Journal 1(2)",
        pdf_url="https://arxiv.org/pdf/2404.22222.pdf",
        html_url="https://arxiv.org/html/2404.22222",
        citation_count=42,
        influential_citation_count=7,
        semantic_scholar_id="s2-detail-paper",
        code_url="https://github.com/example/detail-paper",
        code_url_source="hf_papers",
        code_index_status="indexed",
        code_is_python=True,
        code_python_reason="Python package detected",
        venue="International Conference on Detail Learning",
        venue_year=2026,
        venue_source="s2_bulk",
        top_author_s2_id="author-detail",
        source_type="html",
        index_status="indexed",
        indexed_at=datetime(2026, 4, 22, tzinfo=timezone.utc),
    )
    service.repository.upsert_paper(paper)  # type: ignore[attr-defined]

    detail = service.get_browser_trending_paper("arxiv:2404.22222")

    assert detail.paper_id == "arxiv:2404.22222"
    assert detail.abstract == "Full abstract for a metadata-rich trending paper."
    assert detail.tldr == "Semantic Scholar TLDR"
    assert detail.updated == "2026-04-22"
    assert detail.doi == "10.1234/metadata"
    assert detail.comment == "Accepted at DetailConf"
    assert detail.journal_ref == "Detail Journal 1(2)"
    assert detail.citation_count == 42
    assert detail.influential_citation_count == 7
    assert detail.semantic_scholar_id == "s2-detail-paper"
    assert detail.code_url == "https://github.com/example/detail-paper"
    assert detail.code_url_source == "hf_papers"
    assert detail.code_index_status == "indexed"
    assert detail.code_is_python is True
    assert detail.code_python_reason == "Python package detected"
    assert detail.venue == "International Conference on Detail Learning"
    assert detail.venue_year == 2026
    assert detail.venue_source == "s2_bulk"
    assert detail.top_author_s2_id == "author-detail"
    assert detail.source_type == "html"
    assert detail.index_status == "indexed"
    assert detail.indexed_at == datetime(2026, 4, 22, tzinfo=timezone.utc)
