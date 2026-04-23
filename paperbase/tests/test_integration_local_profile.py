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
def test_search_local_recency_boost_does_not_dominate_lexical_match(integration_context):
    """A recent-but-irrelevant paper must not outrank an older paper with a real
    lexical match. Regression for the recency_boost = 0.02 bug that exceeded
    both the citation_boost cap and the top RRF rank unit (1/60 ≈ 0.017)."""
    _, repository, _, namespace = integration_context
    rare_token = f"{namespace.replace('/', '')}xenotoken"

    relevant_old = PaperMetadata(
        arxiv_id=f"{namespace}/relevant-old",
        title=f"Older paper about {rare_token}",
        authors="Vintage Author",
        abstract=f"This older paper extensively studies {rare_token} mechanisms.",
        categories="cs.AI",
        primary_category="cs.AI",
        published="2017-06-12",
        updated="2017-06-12",
        pdf_url="https://example.test/old.pdf",
        html_url="https://example.test/old",
        citation_count=0,
        index_status="indexed",
    )
    irrelevant_recent = PaperMetadata(
        arxiv_id=f"{namespace}/irrelevant-recent",
        title="Recent paper on unrelated topic",
        authors="Newer Author",
        abstract="Discusses something completely unrelated to the query token.",
        categories="cs.AI",
        primary_category="cs.AI",
        published="2026-04-21",
        updated="2026-04-21",
        pdf_url="https://example.test/new.pdf",
        html_url="https://example.test/new",
        citation_count=0,
        index_status="indexed",
    )
    repository.upsert_paper(relevant_old, search_document=relevant_old.abstract, embedding=None)
    repository.upsert_paper(irrelevant_recent, search_document=irrelevant_recent.abstract, embedding=None)

    results = repository.search_local(rare_token, limit=5, query_embedding=None)
    arxiv_ids = [r.arxiv_id for r in results]

    assert relevant_old.arxiv_id in arxiv_ids, "relevant paper should be in results"
    assert arxiv_ids[0] == relevant_old.arxiv_id, (
        f"recent-but-irrelevant paper outranked the lexical match: {arxiv_ids}"
    )


@pytest.mark.integration
def test_search_local_orders_similarity_fallback_by_relevance_not_citations(integration_context):
    """When candidates fall through the trigram-similarity fallback (no @@ match),
    the SQL must order by similarity to the query, not by raw citation count.
    Otherwise an unrelated highly-cited paper outranks a low-cite paper that
    actually matches the query."""
    _, repository, _, namespace = integration_context
    rare_phrase = f"{namespace.replace('/', '')} sparse niche topic phrase"

    relevant_low_cite = PaperMetadata(
        arxiv_id=f"{namespace}/relevant-lowcite",
        title=f"{rare_phrase}",
        authors="Author A",
        abstract=f"Discusses {rare_phrase}.",
        tldr=f"{rare_phrase} tldr",
        categories="cs.AI",
        primary_category="cs.AI",
        published="2024-01-01",
        updated="2024-01-01",
        pdf_url="https://example.test/a.pdf",
        html_url="https://example.test/a",
        citation_count=0,
        index_status="indexed",
    )
    irrelevant_high_cite = PaperMetadata(
        arxiv_id=f"{namespace}/irrelevant-highcite",
        title="Completely unrelated landmark paper",
        authors="Author B",
        abstract="On a totally different subject.",
        categories="cs.AI",
        primary_category="cs.AI",
        published="2024-01-01",
        updated="2024-01-01",
        pdf_url="https://example.test/b.pdf",
        html_url="https://example.test/b",
        citation_count=200_000,
        index_status="indexed",
    )
    repository.upsert_paper(relevant_low_cite, search_document=relevant_low_cite.abstract, embedding=None)
    repository.upsert_paper(irrelevant_high_cite, search_document=irrelevant_high_cite.abstract, embedding=None)

    results = repository.search_local(rare_phrase, limit=5, query_embedding=None)
    arxiv_ids = [r.arxiv_id for r in results]

    assert relevant_low_cite.arxiv_id in arxiv_ids
    assert arxiv_ids[0] == relevant_low_cite.arxiv_id, (
        f"high-citation unrelated paper outranked the relevant low-cite one: {arxiv_ids}"
    )


@pytest.mark.integration
def test_search_rerank_only_runs_when_request_opts_in(integration_context, monkeypatch):
    """The rerank flag on SearchRequest gates whether the CrossEncoder pass runs,
    even when a rerank model is configured. Default search keeps the fast path."""
    from paperbase.models import SearchRequest

    _, repository, service, namespace = integration_context

    arxiv_id = f"{namespace}/rerank/0001"
    paper = PaperMetadata(
        arxiv_id=arxiv_id,
        title=f"{namespace} rerank paper",
        authors="Author X",
        abstract=f"{namespace} body",
        categories="cs.AI",
        primary_category="cs.AI",
        published="2024-01-01",
        updated="2024-01-01",
        pdf_url="https://example.test/r.pdf",
        html_url="https://example.test/r",
        citation_count=10,
        index_status="indexed",
    )
    repository.upsert_paper(paper, search_document=paper.abstract, embedding=None)

    rerank_calls: list[bool] = []

    class StubReranker:
        def enabled(self) -> bool:
            return True

        @property
        def top_k(self) -> int:
            return 50

        def score(self, query, docs):
            rerank_calls.append(True)
            return [0.5 for _ in docs]

    monkeypatch.setattr(service, "reranker", StubReranker())

    service.search(SearchRequest(query=namespace, limit=5, include_live_sources=False, rerank=False))
    assert rerank_calls == [], "rerank ran despite request.rerank=False"

    service.search(SearchRequest(query=namespace, limit=5, include_live_sources=False, rerank=True))
    assert rerank_calls == [True], "rerank did not run despite request.rerank=True"


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


@pytest.mark.integration
def test_claim_pending_job_is_atomic_across_concurrent_workers(integration_context):
    """Two workers racing on the same pending row must not both claim it.

    Regression test for the retry-policy review on #32: the original
    ``claim_pending_job`` used select-then-mutate, which is a classic
    lost-update race under horizontal scaling — both workers see the
    same pending row, both set ``status='running'``, both proceed to
    process the same job. The fix is ``SELECT ... FOR UPDATE SKIP
    LOCKED`` inside the claim transaction so concurrent sessions jump
    over a row another worker has already locked.

    This exercises the real Postgres path by:

    1. Enqueuing a single pending job.
    2. Opening two separate repository sessions bound to the same DB.
    3. Having one session take the row lock inside an open transaction,
       then a second session attempts a claim before the first commits.
    4. Asserting the second claim sees no pending row (``None``) rather
       than re-claiming the row the first session already has.
    """
    import threading
    from sqlalchemy import or_, select
    from sqlalchemy.orm import Session
    from paperbase.repository import JobORM, SqlAlchemyPaperRepository
    from paperbase.utils import utcnow

    _, repository, _, namespace = integration_context
    assert isinstance(repository, SqlAlchemyPaperRepository), (
        "concurrency test must run against the SQL repository"
    )

    created = repository.create_job("index_paper", {"arxiv_id": f"{namespace}/concurrent-claim"})
    try:
        # First worker: hold the row-lock open in a background thread
        # without committing. While this transaction is alive, any other
        # ``SELECT ... FOR UPDATE SKIP LOCKED`` against the same row
        # MUST skip it — that's the entire guarantee.
        first_claimed: dict[str, JobORM | None] = {"row": None}
        lock_acquired = threading.Event()
        release = threading.Event()

        def hold_lock() -> None:
            with Session(repository._engine) as session:
                row = session.scalar(
                    select(JobORM)
                    .where(
                        JobORM.status == "pending",
                        or_(JobORM.next_run_at.is_(None), JobORM.next_run_at <= utcnow()),
                    )
                    .order_by(JobORM.next_run_at.is_(None).desc(), JobORM.created_at.asc())
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
                first_claimed["row"] = row
                lock_acquired.set()
                release.wait(timeout=10)

        holder = threading.Thread(target=hold_lock, name="first-claimer", daemon=True)
        holder.start()
        assert lock_acquired.wait(timeout=10), "first worker never acquired the row lock"
        assert first_claimed["row"] is not None
        assert first_claimed["row"].job_id == created.job_id

        # Second worker attempts to claim while the first holds the row.
        # The only pending job is locked, so SKIP LOCKED must return None.
        second_claim = repository.claim_pending_job()
        assert second_claim is None, (
            "second worker claimed a job that was already locked by the first — "
            "the SELECT FOR UPDATE SKIP LOCKED guarantee is broken"
        )

        # Release the first worker's transaction; the job is still
        # pending from the DB's perspective (the first holder never
        # mutated), so a post-release claim should succeed.
        release.set()
        holder.join(timeout=10)

        after_release = repository.claim_pending_job()
        assert after_release is not None
        assert after_release.job_id == created.job_id
        assert after_release.status == "running"
    finally:
        with _connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM jobs WHERE job_id = %s", (created.job_id,))
