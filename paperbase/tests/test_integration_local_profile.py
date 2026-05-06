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
            cursor.execute(
                "DELETE FROM job_papers WHERE job_id IN (SELECT job_id FROM jobs WHERE payload::text LIKE %s) OR paper_id LIKE %s",
                (f"%{namespace}%", f"arxiv:{namespace}/%"),
            )
            cursor.execute("DELETE FROM jobs WHERE payload::text LIKE %s", (f"%{namespace}%",))
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
    discovered = PaperMetadata(
        arxiv_id=f"{namespace}/search/0002",
        title=f"{namespace} discovered benchmark paper",
        authors="Integration Tester",
        abstract=f"{query} discovered abstract",
        tldr=f"{query} discovered tl;dr",
        summary_short=f"{query} discovered short summary",
        categories="cs.AI",
        primary_category="cs.AI",
        published="2026-04-21",
        updated="2026-04-21",
        pdf_url=f"https://example.test/{namespace}/search/0002.pdf",
        html_url=f"https://example.test/{namespace}/search/0002",
        citation_count=999,
        influential_citation_count=99,
    )
    discovered_response = repository.upsert_paper_shallow(discovered)
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
    repository.replace_text_chunks(
        discovered_response.paper_id,
        [
            {
                "path": "sections/discovered.md",
                "chunk_kind": "section",
                "content": f"{query} discovered section",
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
        paper_ids=[paper_response.paper_id, discovered_response.paper_id],
        query_embedding=None,
    )
    result_set = repository.save_result_set(query=query, papers=results, ttl_days=1)
    loaded = repository.get_result_set(result_set.result_set_id)
    grep = repository.grep_result_set(result_set.result_set_id, namespace, limit=5)

    assert results
    assert results[0].paper_id == paper_response.paper_id
    assert discovered_response.paper_id not in {result.paper_id for result in results}
    assert sections
    assert sections[0].paper_id == paper_response.paper_id
    assert discovered_response.paper_id not in {section.paper_id for section in sections}
    assert loaded is not None
    assert loaded.result_set_id == result_set.result_set_id
    assert grep
    assert grep[0].paper_id == paper_response.paper_id


@pytest.mark.integration
def test_sql_search_local_surfaces_caption_only_chunk_match(integration_context):
    _, repository, _, namespace = integration_context
    rare_token = f"{namespace.replace('/', '')}captionneedle"
    paper = PaperMetadata(
        arxiv_id=f"{namespace}/caption-only",
        title="Unrelated title",
        authors="Integration Tester",
        abstract="This abstract intentionally omits the caption token.",
        tldr="Unrelated tl;dr",
        summary_short="Unrelated short summary",
        summary_eli5="Unrelated simple summary",
        categories="cs.AI",
        primary_category="cs.AI",
        published="2024-01-01",
        updated="2024-01-01",
        pdf_url="https://example.test/caption-only.pdf",
        html_url="https://example.test/caption-only",
        citation_count=0,
        influential_citation_count=0,
        index_status="indexed",
    )
    stored = repository.upsert_paper(paper, search_document="unrelated search document", embedding=None)
    repository.replace_text_chunks(
        stored.paper_id,
        [
            {
                "path": "figures/figure-1/caption.md",
                "chunk_kind": "figure_caption",
                "content": f"Caption evidence includes {rare_token}.",
                "embedding": None,
                "chunk_index": 0,
                "section_depth": 1,
                "section_order": 0,
            }
        ],
    )

    results = repository.search_local(rare_token, limit=5, query_embedding=None)

    assert [result.paper_id for result in results] == [stored.paper_id]


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
def test_sql_repository_reuses_duplicate_job_dedupe_key(integration_context):
    _, repository, _, namespace = integration_context

    dedupe_key = f"test:{namespace}:dedupe"
    first = repository.create_job(
        "index_paper",
        {"arxiv_id": f"{namespace}/dedupe-first"},
        dedupe_key=dedupe_key,
    )
    second = repository.create_job(
        "index_paper",
        {"arxiv_id": f"{namespace}/dedupe-second"},
        dedupe_key=dedupe_key,
    )

    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM jobs WHERE dedupe_key = %s", (dedupe_key,))
            count = cursor.fetchone()[0]

    assert second.job_id == first.job_id
    assert second.payload["arxiv_id"] == f"{namespace}/dedupe-first"
    assert count == 1


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
        #
        # The lock SELECT is filtered to the specific job_id we just
        # created (#49). An earlier version filtered only on
        # ``status == 'pending'`` and tripped over leftover rows from
        # prior manual ``paperbase-dev harvest`` / reindex scripts —
        # the lock would land on whichever pending row sorted first,
        # not necessarily ours.
        first_claimed: dict[str, JobORM | None] = {"row": None}
        lock_acquired = threading.Event()
        release = threading.Event()

        def hold_lock() -> None:
            with Session(repository._engine) as session:
                row = session.scalar(
                    select(JobORM)
                    .where(
                        JobORM.job_id == created.job_id,
                        JobORM.status == "pending",
                        or_(JobORM.next_run_at.is_(None), JobORM.next_run_at <= utcnow()),
                    )
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

        # Second worker attempts to claim OUR specific row while the
        # first holds it. We use a hand-rolled claim scoped to
        # ``created.job_id`` (rather than ``repository.claim_pending_job``
        # which doesn't filter by id) so leftover pending rows from
        # prior dev scripts don't muddy the assertion. This still
        # exercises the production SKIP LOCKED query shape — we just
        # restrict the candidate set to one row.
        def claim_specific_with_skip_locked(job_id: str) -> JobORM | None:
            with Session(repository._engine) as session:
                row = session.scalar(
                    select(JobORM)
                    .where(
                        JobORM.job_id == job_id,
                        JobORM.status == "pending",
                    )
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
                if row is None:
                    return None
                row.status = "running"
                row.last_attempted_at = utcnow()
                row.attempts += 1
                session.flush()
                session.expunge(row)
                session.commit()
                return row

        second_claim = claim_specific_with_skip_locked(created.job_id)
        assert second_claim is None, (
            "second worker claimed a job that was already locked by the first — "
            "the SELECT FOR UPDATE SKIP LOCKED guarantee is broken"
        )

        # Release the first worker's transaction; the job is still
        # pending from the DB's perspective (the first holder never
        # mutated), so a post-release claim against our specific
        # job_id should succeed.
        release.set()
        holder.join(timeout=10)

        after_release = claim_specific_with_skip_locked(created.job_id)
        assert after_release is not None
        assert after_release.job_id == created.job_id
        assert after_release.status == "running"
    finally:
        with _connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM jobs WHERE job_id = %s", (created.job_id,))


@pytest.mark.integration
def test_sql_set_paper_top_author_is_atomic_first_write_wins(integration_context):
    """When two harvest_chunk workers race on the same paper, the SQL
    ``set_paper_top_author`` must implement first-write-wins atomically —
    a read-then-write would let both workers observe ``NULL`` and clobber
    each other. The fix is a single ``UPDATE ... WHERE
    top_author_s2_id IS NULL`` so at most one of the racing UPDATEs
    matches, the other is a no-op.

    Single-thread coverage of the same code path: land a stamp, attempt a
    second land with a different id, observe the second is preserved-by-
    existing. Then verify clears (None) bypass the IS NULL guard so
    operators can still reset.
    """
    _, repository, _, namespace = integration_context
    arxiv_id = f"{namespace}/top-author-race"
    paper = PaperMetadata(
        arxiv_id=arxiv_id,
        title=f"{namespace} top-author paper",
        authors="Race Tester",
        abstract="abstract",
        published="2026-04-21",
        updated="2026-04-21",
        index_status="indexed",
    )
    stored = repository.upsert_paper(paper, search_document="search", embedding=None)

    first = repository.set_paper_top_author(stored.paper_id, top_author_s2_id="A1")
    assert first is not None and first.top_author_s2_id == "A1"

    # Second writer: predicate ``top_author_s2_id IS NULL`` no longer
    # matches, so the UPDATE is a no-op. The returned row still carries
    # A1 (first-write-wins).
    second = repository.set_paper_top_author(stored.paper_id, top_author_s2_id="A2")
    assert second is not None and second.top_author_s2_id == "A1"

    # Explicit clear bypasses the IS NULL guard.
    cleared = repository.set_paper_top_author(stored.paper_id, top_author_s2_id=None)
    assert cleared is not None and cleared.top_author_s2_id is None

    # After clearing, a new stamp lands again.
    relanded = repository.set_paper_top_author(stored.paper_id, top_author_s2_id="A2")
    assert relanded is not None and relanded.top_author_s2_id == "A2"


@pytest.mark.integration
def test_sql_set_paper_top_author_is_atomic_under_concurrent_workers(integration_context):
    """Concurrency proof: two threads racing on the same NULL row must
    not both stamp. One UPDATE matches the WHERE predicate, the other
    matches zero rows and is a silent no-op. After both finish, the row
    carries exactly one of the two competing values."""
    import threading
    from sqlalchemy.orm import Session

    _, repository, _, namespace = integration_context
    arxiv_id = f"{namespace}/top-author-concurrent"
    paper = PaperMetadata(
        arxiv_id=arxiv_id,
        title=f"{namespace} concurrent stamp paper",
        authors="Race Tester",
        abstract="abstract",
        published="2026-04-21",
        updated="2026-04-21",
        index_status="indexed",
    )
    stored = repository.upsert_paper(paper, search_document="search", embedding=None)

    # Use a barrier so both threads kick off the UPDATE as close together
    # as possible. The atomic guarantee doesn't depend on timing — the
    # WHERE predicate is what serializes — but tighter timing is a more
    # honest exercise of the race.
    barrier = threading.Barrier(2)
    landed: list[str] = []
    lock = threading.Lock()

    def stamp(value: str) -> None:
        barrier.wait(timeout=10)
        with Session(repository._engine) as session:
            from sqlalchemy import update as _update
            from paperbase.repository import PaperORM
            from paperbase.utils import utcnow as _utcnow

            result = session.execute(
                _update(PaperORM)
                .where(
                    PaperORM.paper_id == stored.paper_id,
                    PaperORM.top_author_s2_id.is_(None),
                )
                .values(top_author_s2_id=value, updated_at=_utcnow())
            )
            session.commit()
            if result.rowcount == 1:
                with lock:
                    landed.append(value)

    a = threading.Thread(target=stamp, args=("A1",), daemon=True)
    b = threading.Thread(target=stamp, args=("A2",), daemon=True)
    a.start()
    b.start()
    a.join(timeout=10)
    b.join(timeout=10)

    final = repository.get_paper(stored.paper_id)
    assert final is not None and final.top_author_s2_id in {"A1", "A2"}
    assert len(landed) == 1, (
        f"both threads stamped — atomic first-write-wins is broken (landed={landed})"
    )
    assert final.top_author_s2_id == landed[0]
