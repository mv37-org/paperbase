from __future__ import annotations

from uuid import uuid4

import psycopg
import pytest
from sqlalchemy.engine import make_url

from paperbase.config import get_settings
from paperbase.models import PaperMetadata
from paperbase.repository import build_repository
from paperbase.service import PaperbaseService
from paperbase.storage import build_object_store


def _connect() -> psycopg.Connection:
    settings = get_settings()
    url = make_url(settings.database_url)
    dsn = url.set(drivername="postgresql").render_as_string(hide_password=False)
    return psycopg.connect(dsn, autocommit=True)


def _cleanup_namespace(namespace: str) -> None:
    settings = get_settings()
    build_object_store(settings).delete_prefix(f"papers/arxiv:{namespace}")
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM result_items WHERE result_set_id IN (SELECT result_set_id FROM result_sets WHERE query LIKE %s OR pattern LIKE %s)", (f"{namespace}%", f"{namespace}%"))
            cursor.execute("DELETE FROM result_sets WHERE query LIKE %s OR pattern LIKE %s", (f"{namespace}%", f"{namespace}%"))
            cursor.execute("DELETE FROM jobs WHERE payload::text LIKE %s", (f"%{namespace}/%",))
            cursor.execute("DELETE FROM paper_refs WHERE src_paper_id LIKE %s OR dst_paper_id LIKE %s OR dst_arxiv_id LIKE %s", (f"arxiv:{namespace}/%", f"arxiv:{namespace}/%", f"{namespace}/%",))
            cursor.execute("DELETE FROM text_chunks WHERE paper_id LIKE %s", (f"arxiv:{namespace}/%",))
            cursor.execute("DELETE FROM papers WHERE paper_id LIKE %s OR arxiv_id LIKE %s", (f"arxiv:{namespace}/%", f"{namespace}/%",))


@pytest.fixture
def integration_context():
    settings = get_settings()
    repository = build_repository(settings)
    object_store = build_object_store(settings)
    service = PaperbaseService(settings=settings, repository=repository, object_store=object_store)
    service.init()

    namespace = f"it/{uuid4().hex[:10]}"
    _cleanup_namespace(namespace)
    try:
        yield settings, repository, service, namespace
    finally:
        _cleanup_namespace(namespace)


@pytest.mark.integration
def test_probe_integrations_against_active_local_profile(integration_context):
    _, _, service, _ = integration_context

    probe = service.probe_integrations()

    assert probe.ok is True
    assert probe.database.ok is True
    assert probe.storage.ok is True
    assert probe.storage.metadata["path"].startswith("system/probes/probe_")


@pytest.mark.integration
def test_sql_repository_search_and_result_set_flow(integration_context):
    _, repository, _, namespace = integration_context
    arxiv_id = f"{namespace}/search/0001"
    query = f"{namespace} grounded retrieval"
    paper = PaperMetadata(
        arxiv_id=arxiv_id,
        title=f"{namespace} benchmark paper",
        authors="Integration Tester",
        abstract=f"{query} abstract",
        tldr=f"{query} tl;dr",
        summary_short=f"{query} short summary",
        summary_eli5=f"{query} simple summary",
        categories="cs.AI",
        primary_category="cs.AI",
        published="2026-04-21",
        updated="2026-04-21",
        pdf_url=f"https://example.test/{arxiv_id}.pdf",
        html_url=f"https://example.test/{arxiv_id}",
        citation_count=9,
        influential_citation_count=2,
        index_status="indexed",
    )
    paper_response = repository.upsert_paper(
        paper,
        search_document=f"{query} search document",
        embedding=None,
    )
    repository.replace_text_chunks(
        paper_response.paper_id,
        [
            {
                "path": "sections/methods.md",
                "chunk_kind": "section",
                "content": f"{query} methods section",
                "embedding": None,
                "chunk_index": 0,
                "section_depth": 1,
                "section_order": 0,
            }
        ],
    )

    results = repository.search_local(query, limit=5, query_embedding=None)
    sections = repository.search_sections(
        query,
        limit=5,
        paper_ids=[paper_response.paper_id],
        query_embedding=None,
    )
    result_set = repository.save_result_set(query=query, papers=results, ttl_days=1)
    loaded = repository.get_result_set(result_set.result_set_id)
    grep = repository.grep_result_set(result_set.result_set_id, namespace, limit=5)

    assert results
    assert results[0].paper_id == paper_response.paper_id
    assert sections
    assert sections[0].paper_id == paper_response.paper_id
    assert loaded is not None
    assert loaded.result_set_id == result_set.result_set_id
    assert grep
    assert grep[0].paper_id == paper_response.paper_id


@pytest.mark.integration
def test_worker_processes_summarize_job_with_real_repository(integration_context, monkeypatch):
    _, repository, service, namespace = integration_context
    service.settings = service.settings.model_copy(
        update={
            "summary_provider": "vertex",
            "vertex_project": "test-project",
            "featured_tier_concurrency": 1,
        }
    )
    monkeypatch.setattr(
        "paperbase.summaries.generate_summary_on_demand",
        lambda paper, settings: (
            f"{paper.paper_id} technical summary",
            f"{paper.paper_id} plain summary",
        ),
    )

    arxiv_id = f"{namespace}/summaries/0001"
    paper = PaperMetadata(
        arxiv_id=arxiv_id,
        title=f"{namespace} summarize paper",
        authors="Integration Tester",
        abstract=f"{namespace} abstract",
        categories="cs.AI",
        primary_category="cs.AI",
        published="2026-04-21",
        updated="2026-04-21",
        pdf_url=f"https://example.test/{arxiv_id}.pdf",
        html_url=f"https://example.test/{arxiv_id}",
        citation_count=999_999,
        influential_citation_count=999_999,
        index_status="indexed",
    )
    paper_response = repository.upsert_paper(
        paper,
        search_document=f"{namespace} search document",
        embedding=None,
    )

    job = repository.create_job(
        "summarize_top_cited",
        {
            "limit": 1,
            "namespace": namespace,
        },
    )
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE jobs SET created_at = TIMESTAMPTZ '2000-01-01T00:00:00Z' WHERE job_id = %s",
                (job.job_id,),
            )
    processed = service.process_next_job()
    pending_after = repository.list_papers_needing_summary(limit=10)
    stored = repository.get_paper(paper_response.paper_id)

    assert job.status == "pending"
    assert processed is not None
    assert processed.job_id == job.job_id
    assert processed.status == "completed"
    assert stored is not None
    assert stored.summary_short == f"{paper_response.paper_id} technical summary"
    assert stored.summary_eli5 == f"{paper_response.paper_id} plain summary"
    assert all(paper.paper_id != paper_response.paper_id for paper in pending_after)
