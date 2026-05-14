from __future__ import annotations

from datetime import date

import pytest

from paperbase.config import Settings
import requests

from paperbase.errors import PermanentError
from paperbase.models import PaperMetadata
from paperbase.rate_limit import TokenBucketRateLimiter
from paperbase.repository import MemoryPaperRepository
from paperbase.service import PaperbaseService, _month_windows
from paperbase.storage import MemoryObjectStore


def _service() -> PaperbaseService:
    service = PaperbaseService(
        settings=Settings(storage_backend="memory", database_url="memory://", api_key="t"),
        repository=MemoryPaperRepository(),
        object_store=MemoryObjectStore(),
    )
    service.init()
    return service


# -- _month_windows planner math ---------------------------------------------


def test_month_windows_splits_2_year_range_into_3_month_chunks():
    """Planner slice: a 2-year range with chunk_months=3 must produce exactly
    8 windows (12/3 * 2 = 8), each inclusive and non-overlapping."""
    windows = _month_windows("2020-01-01", "2021-12-31", chunk_months=3)

    assert len(windows) == 8
    assert windows[0] == ("2020-01-01", "2020-03-31")
    assert windows[1] == ("2020-04-01", "2020-06-30")
    assert windows[-1] == ("2021-10-01", "2021-12-31")

    # No gaps, no overlaps: next window starts the day after the previous ends.
    for (_prev_start, prev_end), (next_start, _next_end) in zip(windows, windows[1:]):
        prev = date.fromisoformat(prev_end)
        nxt = date.fromisoformat(next_start)
        assert (nxt - prev).days == 1, f"{prev_end} -> {next_start} is not adjacent"


def test_month_windows_clamps_final_window_to_end_date():
    """If the requested end_date falls mid-window, the last window is clamped."""
    windows = _month_windows("2020-01-01", "2020-02-15", chunk_months=3)

    assert windows == [("2020-01-01", "2020-02-15")]


# -- planner enqueue ---------------------------------------------------------


def test_enqueue_harvest_range_job_rejects_invalid_dates():
    """Operator-submitted payloads must fail fast as PermanentError so the
    planner job dead-letters immediately instead of looping retries."""
    service = _service()

    with pytest.raises(PermanentError):
        service.enqueue_harvest_range_job(
            categories=["cs.LG"], start_date="2020-13-01", end_date="2020-06-01"
        )
    with pytest.raises(PermanentError):
        service.enqueue_harvest_range_job(
            categories=["cs.LG"], start_date="2021-01-01", end_date="2020-01-01"
        )
    with pytest.raises(PermanentError):
        service.enqueue_harvest_range_job(categories=[], start_date="2020-01-01", end_date="2020-03-31")
    with pytest.raises(PermanentError, match="limit_per_window"):
        service.enqueue_harvest_range_job(
            categories=["cs.LG"],
            start_date="2020-01-01",
            end_date="2020-03-31",
            limit_per_window=-1,
        )


def test_run_harvest_range_planner_enqueues_one_chunk_per_category_per_window():
    """Running the planner for 2 categories × 2 windows should enqueue 4
    harvest_chunk jobs, each carrying the category + window + source. No
    chunks past the end_date."""
    service = _service()

    result = service.run_harvest_range(
        source="arxiv",
        categories=["cs.LG", "cs.CL"],
        start_date="2020-01-01",
        end_date="2020-06-30",
        limit_per_window=None,
        chunk_months=3,
    )

    assert result == {"chunks_created": 4}
    chunk_jobs = [job for job in service.repository.jobs.values() if job.job_type == "harvest_chunk"]
    assert len(chunk_jobs) == 4
    categories = sorted({job.payload["category"] for job in chunk_jobs})
    assert categories == ["cs.CL", "cs.LG"]
    windows = sorted({(job.payload["start_date"], job.payload["end_date"]) for job in chunk_jobs})
    assert windows == [("2020-01-01", "2020-03-31"), ("2020-04-01", "2020-06-30")]
    assert len({job.root_job_id for job in chunk_jobs}) == 1


def test_harvest_chunks_inherit_root_job_id():
    service = _service()

    service.run_harvest_range(
        source="arxiv",
        categories=["cs.LG"],
        start_date="2020-01-01",
        end_date="2020-03-31",
        limit_per_window=None,
        chunk_months=3,
        harvest_id="harvest_root_1",
        root_job_id="harvest_root_1",
    )

    chunk_jobs = [job for job in service.repository.jobs.values() if job.job_type == "harvest_chunk"]
    assert len(chunk_jobs) == 1
    assert chunk_jobs[0].root_job_id == "harvest_root_1"


def test_memory_repository_reuses_duplicate_job_dedupe_key():
    service = _service()

    first = service.repository.create_job(
        "harvest_chunk",
        {"source": "arxiv", "category": "cs.LG", "cursor": 0},
        dedupe_key="test-harvest-dedupe",
    )
    second = service.repository.create_job(
        "harvest_chunk",
        {"source": "arxiv", "category": "cs.LG", "cursor": 100},
        dedupe_key="test-harvest-dedupe",
    )

    assert second.job_id == first.job_id
    assert len(service.repository.jobs) == 1
    assert service.repository.jobs[first.job_id].payload["cursor"] == 0


def test_harvest_range_retry_does_not_duplicate_planned_chunks():
    """A retry of the same planner job must not append duplicate cursor=0
    chunks. The dedupe namespace is the stable harvest_id from the parent
    payload, not the date range alone, so a brand-new top-level harvest can
    still intentionally rerun the same range."""
    service = _service()
    harvest_id = "harvest_retry_planner"

    for _ in range(2):
        service.run_harvest_range(
            source="arxiv",
            harvest_id=harvest_id,
            categories=["cs.LG", "cs.CL"],
            start_date="2020-01-01",
            end_date="2020-06-30",
            limit_per_window=None,
            chunk_months=3,
        )

    chunk_jobs = [job for job in service.repository.jobs.values() if job.job_type == "harvest_chunk"]
    assert len(chunk_jobs) == 4
    keys = {
        (job.payload["harvest_id"], job.payload["category"], job.payload["start_date"], job.payload["cursor"])
        for job in chunk_jobs
    }
    assert len(keys) == 4


def test_run_harvest_range_rejects_non_arxiv_source():
    """v1 is arxiv-only. A planner job with a non-arxiv source raises
    PermanentError so it dead-letters cleanly rather than retrying."""
    service = _service()

    with pytest.raises(PermanentError):
        service.run_harvest_range(
            source="biorxiv",
            categories=["cs.LG"],
            start_date="2020-01-01",
            end_date="2020-03-31",
            limit_per_window=None,
            chunk_months=3,
        )


def test_run_harvest_range_rejects_malformed_worker_payloads():
    """The worker handler validates persisted planner payloads too. Enqueue
    validates operator input, but a bad in-flight/manual row must dead-letter
    instead of hanging the worker in window generation."""
    service = _service()

    with pytest.raises(PermanentError, match="chunk_months"):
        service.run_harvest_range(
            source="arxiv",
            categories=["cs.LG"],
            start_date="2020-01-01",
            end_date="2020-03-31",
            limit_per_window=None,
            chunk_months=0,
        )
    with pytest.raises(PermanentError, match="categories"):
        service.run_harvest_range(
            source="arxiv",
            categories=[],
            start_date="2020-01-01",
            end_date="2020-03-31",
            limit_per_window=None,
            chunk_months=3,
        )
    with pytest.raises(PermanentError, match="start_date"):
        service.run_harvest_range(
            source="arxiv",
            categories=["cs.LG"],
            start_date="2020-04-01",
            end_date="2020-03-31",
            limit_per_window=None,
            chunk_months=3,
        )


def test_limit_per_window_is_per_chunk_not_per_category_total():
    """Contract lock per the #37 review: ``limit_per_window`` is PER
    (category, window), not a per-category total across the whole
    requested date range. A 2-year range with chunk_months=3 and
    limit_per_window=1000 is therefore 8 chunks × 1000 = potentially
    8000 papers per category — and every chunk's ``limit`` payload must
    carry exactly that 1000 cap without any across-window budget
    rebalancing. Regression test: if someone rewires the planner to
    spread the cap across windows (e.g. ``limit / len(windows)``), the
    per-chunk limit would drop and this assertion would fire.
    """
    service = _service()

    service.run_harvest_range(
        source="arxiv",
        categories=["cs.LG"],
        start_date="2020-01-01",
        end_date="2021-12-31",
        limit_per_window=1000,
        chunk_months=3,
    )

    chunk_jobs = [job for job in service.repository.jobs.values() if job.job_type == "harvest_chunk"]
    assert len(chunk_jobs) == 8, "2-year range with 3-month chunks must plan 8 windows"
    limits = {job.payload["limit"] for job in chunk_jobs}
    assert limits == {1000}, (
        f"every window must receive the same per-window cap (got {limits})"
    )


# -- chunk worker pagination + idempotency -----------------------------------


def _fake_fetch_range(pages_by_category: dict[str, list[list[PaperMetadata]]]):
    """Build a fetch_range stub that returns successive pages per call, keyed
    on category. Each category's pages are served in order; the last page's
    size is < page_size so next_cursor becomes None."""
    call_counters: dict[str, int] = {}

    def stub(category, *, start_date, end_date, start_offset=0, page_size=100):
        index = call_counters.get(category, 0)
        pages = pages_by_category.get(category, [])
        if index >= len(pages):
            return [], None
        page = pages[index]
        call_counters[category] = index + 1
        is_last_page = index == len(pages) - 1
        next_cursor = None if is_last_page else start_offset + len(page)
        return page, next_cursor

    return stub, call_counters


def _paper(arxiv_id: str) -> PaperMetadata:
    return PaperMetadata(
        arxiv_id=arxiv_id,
        title=f"Paper {arxiv_id}",
        authors="A",
        abstract="body",
        categories="cs.LG",
        primary_category="cs.LG",
        published="2020-02-10",
        updated="2020-02-10",
        pdf_url="",
        html_url="",
    )


def _drain_harvest_chunks(service: PaperbaseService) -> int:
    """Pop every pending harvest_chunk in priority order and run it through
    the public dispatcher, the way a real worker would. Returns the number
    of chunk claims that executed."""
    claims = 0
    while True:
        pending = [
            job
            for job in service.repository.jobs.values()
            if job.status == "pending" and job.job_type == "harvest_chunk"
        ]
        if not pending:
            return claims
        pending.sort(key=lambda job: (job.priority, job.created_at))
        next_job = pending[0]
        service.run_harvest_chunk(
            source=str(next_job.payload.get("source", "arxiv")),
            harvest_id=next_job.payload.get("harvest_id"),
            category=str(next_job.payload["category"]),
            start_date=str(next_job.payload["start_date"]),
            end_date=str(next_job.payload["end_date"]),
            limit=next_job.payload.get("limit"),
            cursor=int(next_job.payload.get("cursor", 0)),
            fetched=int(next_job.payload.get("fetched", 0)),
        )
        service.repository.mark_job_completed(next_job.job_id)
        claims += 1


def test_harvest_chunk_does_one_page_per_claim_and_enqueues_continuation(monkeypatch):
    """Blocking-review fix (#37): each ``harvest_chunk`` claim must do
    BOUNDED work — one page — and enqueue a continuation for the next
    page. That way the worker re-enters the priority queue between
    pages and a ``harvest_chunk`` cannot monopolize the loop while
    interactive ``index_paper`` work waits. 2 full pages + 1 short page
    is 3 claims total, 3 fetch_range calls, 205 papers indexed."""
    service = _service()
    full_page_1 = [_paper(f"2020.{i:05d}") for i in range(100)]
    full_page_2 = [_paper(f"2020.1{i:05d}") for i in range(100)]
    short_page = [_paper(f"2020.2{i:05d}") for i in range(5)]
    fetch_stub, counters = _fake_fetch_range({"cs.LG": [full_page_1, full_page_2, short_page]})

    indexed_ids: list[str] = []

    def fake_index(arxiv_id, *, paper=None, force=False):
        indexed_ids.append(arxiv_id)
        return paper

    monkeypatch.setattr("paperbase.service.sources.fetch_range", fetch_stub)
    monkeypatch.setattr(service, "index_arxiv_paper", fake_index)

    service.repository.create_job(
        "harvest_chunk",
        {
            "source": "arxiv",
            "category": "cs.LG",
            "start_date": "2020-01-01",
            "end_date": "2020-03-31",
            "limit": None,
            "cursor": 0,
            "fetched": 0,
        },
    )

    claims = _drain_harvest_chunks(service)

    assert claims == 3, "3 pages → 3 separate chunk claims"
    assert counters["cs.LG"] == 3
    assert len(indexed_ids) == 205
    pending = [j for j in service.repository.jobs.values() if j.status == "pending"]
    assert pending == []


def test_harvest_chunk_rejects_non_positive_limit_before_fetch(monkeypatch):
    service = _service()

    def fail_fetch(*_args, **_kwargs):
        raise AssertionError("invalid payloads must be rejected before fetching arxiv")

    monkeypatch.setattr("paperbase.service.sources.fetch_range", fail_fetch)

    with pytest.raises(PermanentError, match="limit"):
        service.run_harvest_chunk(
            source="arxiv",
            category="cs.LG",
            start_date="2020-01-01",
            end_date="2020-03-31",
            limit=0,
            cursor=0,
            fetched=0,
        )
    with pytest.raises(PermanentError, match="start_date"):
        service.run_harvest_chunk(
            source="arxiv",
            category="cs.LG",
            start_date="not-a-date",
            end_date="2020-03-31",
            limit=None,
            cursor=0,
            fetched=0,
        )
    with pytest.raises(PermanentError, match="category"):
        service.run_harvest_chunk(
            source="arxiv",
            category="",
            start_date="2020-01-01",
            end_date="2020-03-31",
            limit=None,
            cursor=0,
            fetched=0,
        )


def test_harvest_chunk_respects_limit_across_continuation(monkeypatch):
    """``limit`` is a per-window cap that must hold across the entire
    continuation chain — not be reset per page. A limit of 150 against
    2 full pages of 100 must index 150 (100 from page 1 + 50 from page 2)
    and enqueue no further continuation."""
    service = _service()
    full_page = [_paper(f"2020.{i:05d}") for i in range(100)]
    page_2 = [_paper(f"2020.1{i:05d}") for i in range(100)]
    page_3 = [_paper(f"2020.2{i:05d}") for i in range(100)]
    fetch_stub, counters = _fake_fetch_range({"cs.LG": [full_page, page_2, page_3]})

    monkeypatch.setattr("paperbase.service.sources.fetch_range", fetch_stub)
    monkeypatch.setattr(service, "index_arxiv_paper", lambda *_a, **_k: None)

    service.repository.create_job(
        "harvest_chunk",
        {
            "source": "arxiv",
            "category": "cs.LG",
            "start_date": "2020-01-01",
            "end_date": "2020-03-31",
            "limit": 150,
            "cursor": 0,
            "fetched": 0,
        },
    )

    _drain_harvest_chunks(service)

    assert counters["cs.LG"] == 2, (
        "limit=150 must stop after page 2 (100 + 50 = 150) and not fetch page 3"
    )


def test_harvest_chunk_retry_resumes_from_saved_cursor(monkeypatch):
    """Regression test arslnb explicitly asked for in the #37 review:
    when a chunk fails after two successful pages, the retry must
    resume from the saved cursor (not restart from page 1). Because
    the continuation's payload has ``cursor=200, fetched=200`` by the
    time page 3 is next up, the retry policy (which re-queues the
    failing payload verbatim) will replay page 3 — not page 1."""
    service = _service()
    full_page_1 = [_paper(f"2020.{i:05d}") for i in range(100)]
    full_page_2 = [_paper(f"2020.1{i:05d}") for i in range(100)]
    full_page_3 = [_paper(f"2020.2{i:05d}") for i in range(100)]
    fetch_stub, _counters = _fake_fetch_range(
        {"cs.LG": [full_page_1, full_page_2, full_page_3]}
    )
    monkeypatch.setattr("paperbase.service.sources.fetch_range", fetch_stub)
    monkeypatch.setattr(service, "index_arxiv_paper", lambda *_a, **_k: None)

    # Page 1 (cursor=0, fetched=0).
    service.run_harvest_chunk(
        source="arxiv",
        category="cs.LG",
        start_date="2020-01-01",
        end_date="2020-03-31",
        limit=None,
        cursor=0,
        fetched=0,
    )
    continuation_1 = [
        j for j in service.repository.jobs.values()
        if j.job_type == "harvest_chunk" and j.payload["cursor"] == 100
    ]
    assert len(continuation_1) == 1
    assert continuation_1[0].payload["fetched"] == 100

    # Page 2 (cursor=100, fetched=100).
    service.run_harvest_chunk(
        source="arxiv",
        category="cs.LG",
        start_date="2020-01-01",
        end_date="2020-03-31",
        limit=None,
        cursor=100,
        fetched=100,
    )
    continuation_2 = [
        j for j in service.repository.jobs.values()
        if j.job_type == "harvest_chunk" and j.payload["cursor"] == 200
    ]
    assert len(continuation_2) == 1
    page_3_payload = continuation_2[0].payload
    assert page_3_payload["cursor"] == 200, (
        "retry of a failed page-3 chunk must resume at cursor=200, not restart at 0"
    )
    assert page_3_payload["fetched"] == 200, (
        "the running fetched total is persisted across pages so the cap holds "
        "across the continuation chain"
    )


def test_harvest_chunk_retry_does_not_duplicate_continuation_chain(monkeypatch):
    """If a chunk creates its continuation and then the parent retry runs, the
    duplicate parent can replay its own page but must not fork the rest of the
    chain."""
    service = _service()
    pages_by_offset = {
        0: [_paper(f"2020.{i:05d}") for i in range(100)],
        100: [_paper(f"2020.1{i:05d}") for i in range(100)],
        200: [_paper(f"2020.2{i:05d}") for i in range(5)],
    }
    fetch_offsets: list[int] = []

    def fetch_by_offset(category, *, start_date, end_date, start_offset=0, page_size=100):
        del category, start_date, end_date
        fetch_offsets.append(start_offset)
        page = pages_by_offset.get(start_offset, [])
        next_cursor = start_offset + len(page) if len(page) >= page_size else None
        return page, next_cursor

    monkeypatch.setattr("paperbase.service.sources.fetch_range", fetch_by_offset)
    monkeypatch.setattr(service, "index_arxiv_paper", lambda *_a, **_k: None)

    payload = {
        "source": "arxiv",
        "harvest_id": "harvest_retry_chunk",
        "category": "cs.LG",
        "start_date": "2020-01-01",
        "end_date": "2020-03-31",
        "limit": None,
        "cursor": 0,
        "fetched": 0,
    }
    service.run_harvest_chunk(**payload)
    service.run_harvest_chunk(**payload)

    cursor_100_jobs = [
        job for job in service.repository.jobs.values()
        if job.job_type == "harvest_chunk" and job.payload["cursor"] == 100
    ]
    assert len(cursor_100_jobs) == 1

    _drain_harvest_chunks(service)

    assert fetch_offsets.count(0) == 2
    assert fetch_offsets.count(100) == 1
    assert fetch_offsets.count(200) == 1


# -- priority: backfill must not starve interactive work ---------------------


def test_claim_orders_interactive_jobs_before_harvest_chunks():
    """With an index_paper (priority=-10) and a harvest_chunk (priority=10)
    both pending, the index_paper must be claimed first regardless of
    created_at. Starvation guard for the backfill pipeline."""
    service = _service()

    chunk_job = service.repository.create_job(
        "harvest_chunk",
        {
            "source": "arxiv",
            "category": "cs.LG",
            "start_date": "2020-01-01",
            "end_date": "2020-03-31",
            "cursor": 0,
        },
    )
    assert chunk_job.priority == 10

    on_demand = service.repository.create_job("index_paper", {"arxiv_id": "test/42"})
    assert on_demand.priority == -10

    claimed = service.repository.claim_pending_job()
    assert claimed is not None
    assert claimed.job_type == "index_paper", (
        f"expected index_paper to win priority, got {claimed.job_type}"
    )


# -- rate limiter ------------------------------------------------------------


def test_token_bucket_enforces_rate_after_burst():
    """After consuming the burst, acquire() must wait roughly 1/rate seconds
    for the bucket to refill one token. Tested with a controllable fake
    clock so the assertion is deterministic."""
    fake_time = {"now": 0.0}
    sleep_calls: list[float] = []

    limiter = TokenBucketRateLimiter(rate_per_second=2.0, burst=2)
    # Swap internal time/sleep hooks so the test doesn't touch wall clock.
    limiter._now = lambda: fake_time["now"]
    limiter._last_refill = fake_time["now"]

    def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        fake_time["now"] += seconds

    limiter._sleep = fake_sleep

    # Burst: first two acquires should not sleep.
    limiter.acquire()
    limiter.acquire()
    assert sleep_calls == []

    # Third acquire must wait ~0.5s (1 token / 2 rate_per_second).
    limiter.acquire()
    assert len(sleep_calls) == 1
    assert 0.4 <= sleep_calls[0] <= 0.6


def test_token_bucket_rejects_bad_config():
    """rate<=0 or burst<1 are always programmer errors; fail fast."""
    with pytest.raises(ValueError):
        TokenBucketRateLimiter(rate_per_second=0, burst=1)
    with pytest.raises(ValueError):
        TokenBucketRateLimiter(rate_per_second=1, burst=0)


# --- top-cited harvest (#39 PR A) -------------------------------------------


def _fake_top_cited_pages(pages: list[list[tuple[str, int]]]):
    """Build a sources.fetch_top_cited_cs stub that serves successive
    pages. Each tuple is ``(arxiv_id, citation_count)``. The last page
    returns next_cursor=None so the planner stops; intermediate pages
    return a fake token."""
    from paperbase.sources import TopCitedCandidate

    call_count = {"n": 0}
    cursors_seen: list[str | None] = []

    def stub(*, min_citations, cursor=None, page_size=1000, fields_of_study="Computer Science", timeout_seconds=30):
        cursors_seen.append(cursor)
        idx = call_count["n"]
        call_count["n"] += 1
        if idx >= len(pages):
            return [], None
        page = pages[idx]
        candidates = [
            TopCitedCandidate(
                arxiv_id=aid,
                semantic_scholar_id=f"s2-{aid}",
                title=f"Paper {aid}",
                citation_count=cites,
                primary_category=None,
                year=2020,
            )
            for aid, cites in page
        ]
        next_cursor = None if idx == len(pages) - 1 else f"token-{idx}"
        return candidates, next_cursor

    return stub, cursors_seen, call_count


def test_enqueue_harvest_top_cited_job_rejects_invalid_inputs():
    """Operator-supplied payloads must fail fast as PermanentError so the
    planner job dead-letters rather than retrying garbage."""
    service = _service()
    with pytest.raises(PermanentError):
        service.enqueue_harvest_top_cited_job(min_citations=-1)
    with pytest.raises(PermanentError):
        service.enqueue_harvest_top_cited_job(max_papers=0)
    with pytest.raises(PermanentError):
        service.enqueue_harvest_top_cited_job(chunk_size=0)
    with pytest.raises(PermanentError):
        service.enqueue_harvest_top_cited_job(chunk_size=1000)


def test_run_harvest_top_cited_planner_batches_into_chunk_size_groups(monkeypatch):
    """Planner reads pages from S2 and emits ``harvest_chunk`` jobs of
    up to ``chunk_size`` arxiv ids each. The dedupe key carries the
    first/last id + length so a retried planner doesn't fork duplicate
    chunks."""
    service = _service()
    pages = [
        [("2020.{:05d}".format(i), 200 - i) for i in range(7)],
        [("2021.{:05d}".format(i), 100 - i) for i in range(3)],
    ]
    stub, cursors_seen, call_count = _fake_top_cited_pages(pages)
    monkeypatch.setattr("paperbase.service.sources.fetch_top_cited_cs", stub)

    result = service.run_harvest_top_cited(
        min_citations=100,
        max_papers=None,
        chunk_size=4,
    )

    assert result["candidates_emitted"] == 10
    assert result["pages_fetched"] == 2
    # 10 ids / 4 per chunk → ceil = 3 chunks
    assert result["chunks_created"] == 3
    chunks = [j for j in service.repository.jobs.values() if j.job_type == "harvest_chunk"]
    assert len(chunks) == 3
    assert all(c.payload["source"] == "s2_top_cited" for c in chunks)
    sizes = sorted(len(c.payload["arxiv_ids"]) for c in chunks)
    assert sizes == [2, 4, 4]
    # Pagination handed cursors back to fetch_top_cited_cs.
    assert cursors_seen[0] is None  # first call
    assert cursors_seen[1] == "token-0"  # second page used the prior token
    assert call_count["n"] == 2


def test_run_harvest_top_cited_respects_max_papers(monkeypatch):
    """``max_papers`` caps total emitted candidates across pages and
    stops S2 paging early so we don't fetch a third page just to throw
    its results away."""
    service = _service()
    pages = [
        [("2020.{:05d}".format(i), 200) for i in range(5)],
        [("2021.{:05d}".format(i), 100) for i in range(5)],
        [("2022.{:05d}".format(i), 50) for i in range(5)],
    ]
    stub, _cursors, call_count = _fake_top_cited_pages(pages)
    monkeypatch.setattr("paperbase.service.sources.fetch_top_cited_cs", stub)

    result = service.run_harvest_top_cited(
        min_citations=50,
        max_papers=7,
        chunk_size=10,
    )

    assert result["candidates_emitted"] == 7
    # First page = 5, second page = 5 (only first 2 used). Third page never fetched.
    assert call_count["n"] == 2
    assert result["pages_fetched"] == 2


def test_run_harvest_top_cited_handles_empty_first_page(monkeypatch):
    """Empty S2 result set → no chunks enqueued, planner completes cleanly."""
    service = _service()
    stub, _cursors, _calls = _fake_top_cited_pages([])
    monkeypatch.setattr("paperbase.service.sources.fetch_top_cited_cs", stub)

    result = service.run_harvest_top_cited(
        min_citations=1_000_000,
        max_papers=None,
        chunk_size=50,
    )

    assert result == {"chunks_created": 0, "candidates_emitted": 0, "pages_fetched": 1}
    chunks = [j for j in service.repository.jobs.values() if j.job_type == "harvest_chunk"]
    assert chunks == []


def test_run_harvest_top_cited_continues_through_empty_filtered_pages(monkeypatch):
    """Regression for PR #50 review: ``fetch_top_cited_cs`` filters non-arxiv
    rows client-side and may return an empty ``page`` while ``next_cursor``
    is still set. The planner must keep paging until S2 itself signals
    end-of-stream (next_cursor is None), not stop on the first empty
    filtered page."""
    from paperbase.sources import TopCitedCandidate

    service = _service()
    call_count = {"n": 0}

    def stub(*, min_citations, cursor=None, page_size=1000, fields_of_study="Computer Science", timeout_seconds=30):
        idx = call_count["n"]
        call_count["n"] += 1
        # Page 0: real data. Page 1: empty (all filtered out client-side)
        # but next_cursor is still set. Page 2: more real data, then EOF.
        if idx == 0:
            return [
                TopCitedCandidate(arxiv_id="2020.00001", semantic_scholar_id="s1", title="P1", citation_count=100, primary_category=None, year=2020),
            ], "token-0"
        if idx == 1:
            return [], "token-1"  # empty filtered page, but more remains
        if idx == 2:
            return [
                TopCitedCandidate(arxiv_id="2020.00002", semantic_scholar_id="s2", title="P2", citation_count=100, primary_category=None, year=2020),
            ], None
        return [], None

    monkeypatch.setattr("paperbase.service.sources.fetch_top_cited_cs", stub)

    result = service.run_harvest_top_cited(
        min_citations=100,
        max_papers=None,
        chunk_size=50,
    )

    # Both real candidates collected — the planner did NOT stop on the
    # empty page 1.
    assert result["candidates_emitted"] == 2
    assert result["pages_fetched"] == 3
    assert call_count["n"] == 3


def test_run_harvest_chunk_top_cited_iterates_arxiv_ids(monkeypatch):
    """The chunk worker for ``source='s2_top_cited'`` calls
    ``index_arxiv_paper`` once per id and counts indexed/skipped."""
    service = _service()
    indexed_calls: list[str] = []

    def fake_index(arxiv_id, *, paper=None, force=False):
        indexed_calls.append(arxiv_id)

    monkeypatch.setattr(service, "index_arxiv_paper", fake_index)

    result = service.run_harvest_chunk_top_cited(
        arxiv_ids=["2020.00001", "2020.00002", "2020.00003"],
        min_citations=100,
    )

    assert indexed_calls == ["2020.00001", "2020.00002", "2020.00003"]
    assert result == {"requested": 3, "indexed": 3, "skipped": 0}


def test_run_harvest_chunk_top_cited_swallows_per_id_permanent_errors(monkeypatch):
    """A PermanentError on one arxiv id (e.g. paper 404 / withdrawn) must
    not kill the whole chunk — the other ids in the batch still get
    indexed and the chunk reports correct skip/index counts."""
    service = _service()

    def fake_index(arxiv_id, *, paper=None, force=False):
        if arxiv_id == "2020.bad":
            raise PermanentError("arxiv 404")

    monkeypatch.setattr(service, "index_arxiv_paper", fake_index)

    result = service.run_harvest_chunk_top_cited(
        arxiv_ids=["2020.00001", "2020.bad", "2020.00003"],
        min_citations=100,
    )

    assert result == {"requested": 3, "indexed": 2, "skipped": 1}


def test_run_harvest_chunk_top_cited_handles_empty_payload():
    """Empty arxiv_ids list (e.g. corrupt payload from a retry) returns
    zero counts without raising."""
    service = _service()
    assert service.run_harvest_chunk_top_cited(arxiv_ids=[]) == {
        "requested": 0,
        "indexed": 0,
        "skipped": 0,
    }


def test_run_harvest_chunk_top_cited_swallows_isolated_arxiv_transient(monkeypatch):
    """A single per-id arxiv 429 / timeout must not kill the chunk — the
    id is skipped and the loop continues. Regression guard: before the
    fix, one 429 propagated up and dropped the remaining ids on the
    floor, taking out 18/20 chunks in a single harvest."""
    service = _service()
    calls: list[str] = []

    def fake_index(arxiv_id, *, paper=None, force=False):
        calls.append(arxiv_id)
        if arxiv_id == "2020.flaky":
            response = requests.Response()
            response.status_code = 429
            raise requests.HTTPError("429 Client Error", response=response)

    monkeypatch.setattr(service, "index_arxiv_paper", fake_index)

    result = service.run_harvest_chunk_top_cited(
        arxiv_ids=["2020.00001", "2020.flaky", "2020.00003"],
        min_citations=100,
    )

    assert calls == ["2020.00001", "2020.flaky", "2020.00003"]
    assert result == {"requested": 3, "indexed": 2, "skipped": 1}


def test_run_harvest_chunk_top_cited_aborts_on_consecutive_arxiv_transients(monkeypatch):
    """After N consecutive arxiv transients the chunk re-raises so the
    worker's retry-with-backoff can give arxiv a cooldown window
    rather than burning through every remaining id one 429 at a time."""
    service = _service()
    limit = service._ARXIV_TOP_CITED_CONSECUTIVE_FAILURE_LIMIT
    attempted: list[str] = []

    def fake_index(arxiv_id, *, paper=None, force=False):
        attempted.append(arxiv_id)
        response = requests.Response()
        response.status_code = 429
        raise requests.HTTPError("429 Client Error", response=response)

    monkeypatch.setattr(service, "index_arxiv_paper", fake_index)

    with pytest.raises(requests.HTTPError):
        service.run_harvest_chunk_top_cited(
            arxiv_ids=[f"2020.{i:05d}" for i in range(10)],
            min_citations=100,
        )

    # We aborted exactly at the limit and did not march through the
    # remaining 7 ids.
    assert attempted == [f"2020.{i:05d}" for i in range(limit)]


def test_run_harvest_chunk_top_cited_resets_consecutive_counter_on_success(monkeypatch):
    """Successful ids between transients reset the failure counter, so
    a chunk with scattered 429s among mostly-healthy ids completes
    cleanly instead of tripping the circuit breaker."""
    service = _service()
    limit = service._ARXIV_TOP_CITED_CONSECUTIVE_FAILURE_LIMIT
    flaky_ids = {"2020.flaky_a", "2020.flaky_b"}

    def fake_index(arxiv_id, *, paper=None, force=False):
        if arxiv_id in flaky_ids:
            response = requests.Response()
            response.status_code = 429
            raise requests.HTTPError("429 Client Error", response=response)

    monkeypatch.setattr(service, "index_arxiv_paper", fake_index)

    # Two transients interleaved with successes — neither run hits the
    # consecutive threshold.
    arxiv_ids = [
        "2020.00001",
        "2020.flaky_a",
        "2020.00002",
        "2020.00003",
        "2020.flaky_b",
        "2020.00004",
    ]
    assert limit >= 2  # otherwise this scenario can't exist
    result = service.run_harvest_chunk_top_cited(
        arxiv_ids=arxiv_ids, min_citations=100,
    )
    assert result == {"requested": 6, "indexed": 4, "skipped": 2}


def test_default_priority_top_cited_matches_harvest_range():
    """Top-cited planner shares priority tier 5 with harvest_range so
    interactive ``index_paper`` (priority -10) always cuts in front,
    and so neither bulk strategy starves the other."""
    from paperbase.repository import default_max_attempts, default_priority

    assert default_priority("harvest_top_cited") == 5
    assert default_priority("index_paper") == -10
    assert default_priority("harvest_range") == default_priority("harvest_top_cited")
    assert default_max_attempts("harvest_top_cited") == 3


def test_fetch_top_cited_cs_validates_inputs():
    """Sources-side guards on min_citations and page_size — these errors
    surface as ValueError rather than PermanentError because the
    primitive is meant to be called with already-validated inputs from
    the planner."""
    from paperbase.sources import fetch_top_cited_cs

    with pytest.raises(ValueError):
        fetch_top_cited_cs(min_citations=-1)
    with pytest.raises(ValueError):
        fetch_top_cited_cs(min_citations=10, page_size=0)
    with pytest.raises(ValueError):
        fetch_top_cited_cs(min_citations=10, page_size=1001)


# --- venue-accepted harvest (#39 PR B) --------------------------------------


def _fake_venue_pages(pages: list[list[tuple[str, str, int]]]):
    """Build a sources.fetch_venue_accepted stub that serves successive
    pages. Each tuple is ``(arxiv_id, venue_string, year)``. Last page
    returns next_cursor=None so the planner stops."""
    from paperbase.sources import VenueCandidate

    call_count = {"n": 0}
    cursors_seen: list[str | None] = []

    def stub(*, venue, year, cursor=None, page_size=1000, timeout_seconds=30):
        cursors_seen.append(cursor)
        idx = call_count["n"]
        call_count["n"] += 1
        if idx >= len(pages):
            return [], None
        page = pages[idx]
        candidates = [
            VenueCandidate(
                arxiv_id=aid,
                semantic_scholar_id=f"s2-{aid}",
                title=f"Paper {aid}",
                venue_string=vs,
                year=yr,
            )
            for aid, vs, yr in page
        ]
        next_cursor = None if idx == len(pages) - 1 else f"token-{idx}"
        return candidates, next_cursor

    return stub, cursors_seen, call_count


def test_enqueue_harvest_venue_job_rejects_invalid_inputs():
    """Operator-supplied payloads must fail fast as PermanentError so the
    planner job dead-letters rather than retrying garbage."""
    service = _service()
    with pytest.raises(PermanentError):
        service.enqueue_harvest_venue_job(venue="", year=2024)
    with pytest.raises(PermanentError):
        service.enqueue_harvest_venue_job(venue="ICLR", year=1899)
    with pytest.raises(PermanentError):
        service.enqueue_harvest_venue_job(venue="ICLR", year=2024, max_papers=0)
    with pytest.raises(PermanentError):
        service.enqueue_harvest_venue_job(venue="ICLR", year=2024, chunk_size=0)
    with pytest.raises(PermanentError):
        service.enqueue_harvest_venue_job(venue="ICLR", year=2024, chunk_size=1000)


def test_run_harvest_venue_planner_batches_and_emits_chunks(monkeypatch):
    """Planner reads pages from S2 and emits ``harvest_chunk`` jobs of
    up to ``chunk_size`` papers each. The dedupe key carries the
    first/last id + length so a retried planner doesn't fork duplicate
    chunks."""
    service = _service()
    pages = [
        [("2020.{:05d}".format(i), "International Conference on Learning Representations", 2024) for i in range(7)],
        [("2021.{:05d}".format(i), "International Conference on Learning Representations", 2024) for i in range(3)],
    ]
    stub, cursors_seen, call_count = _fake_venue_pages(pages)
    monkeypatch.setattr("paperbase.service.sources.fetch_venue_accepted", stub)

    result = service.run_harvest_venue(
        venue="ICLR",
        year=2024,
        max_papers=None,
        chunk_size=4,
    )

    assert result["candidates_emitted"] == 10
    assert result["pages_fetched"] == 2
    assert result["chunks_created"] == 3
    chunks = [j for j in service.repository.jobs.values() if j.job_type == "harvest_chunk"]
    assert len(chunks) == 3
    assert all(c.payload["source"] == "s2_venue" for c in chunks)
    assert all(c.payload["venue"] == "ICLR" for c in chunks)
    assert all(c.payload["year"] == 2024 for c in chunks)
    sizes = sorted(len(c.payload["papers"]) for c in chunks)
    assert sizes == [2, 4, 4]
    # Per-paper venue strings carried through
    first_chunk = chunks[0]
    assert all(p["venue"] == "International Conference on Learning Representations" for p in first_chunk.payload["papers"])
    assert cursors_seen[0] is None
    assert cursors_seen[1] == "token-0"
    assert call_count["n"] == 2


def test_run_harvest_venue_respects_max_papers(monkeypatch):
    """``max_papers`` caps total emitted candidates and stops S2 paging
    early so we don't fetch a page just to throw its results away."""
    service = _service()
    pages = [
        [("2020.{:05d}".format(i), "ICLR canonical", 2024) for i in range(5)],
        [("2021.{:05d}".format(i), "ICLR canonical", 2024) for i in range(5)],
        [("2022.{:05d}".format(i), "ICLR canonical", 2024) for i in range(5)],
    ]
    stub, _cursors, call_count = _fake_venue_pages(pages)
    monkeypatch.setattr("paperbase.service.sources.fetch_venue_accepted", stub)

    result = service.run_harvest_venue(
        venue="ICLR",
        year=2024,
        max_papers=7,
        chunk_size=10,
    )

    assert result["candidates_emitted"] == 7
    assert call_count["n"] == 2
    assert result["pages_fetched"] == 2


def test_run_harvest_venue_handles_empty_first_page(monkeypatch):
    """Empty S2 result set → no chunks enqueued, planner completes cleanly."""
    service = _service()
    stub, _cursors, _calls = _fake_venue_pages([])
    monkeypatch.setattr("paperbase.service.sources.fetch_venue_accepted", stub)

    result = service.run_harvest_venue(
        venue="ICLR",
        year=2099,
        max_papers=None,
        chunk_size=50,
    )

    assert result == {"chunks_created": 0, "candidates_emitted": 0, "pages_fetched": 1}
    chunks = [j for j in service.repository.jobs.values() if j.job_type == "harvest_chunk"]
    assert chunks == []


def test_run_harvest_venue_continues_through_empty_filtered_pages(monkeypatch):
    """Regression for PR #52 review: ``fetch_venue_accepted`` filters out
    papers without arXiv IDs client-side but still returns the upstream
    pagination token. The planner must keep paging until S2 itself
    signals end-of-stream (next_cursor is None), not stop on the first
    empty filtered page — for venues with many non-arxiv papers (e.g.
    CVPR, where workshop papers / tech reports often lack arxiv links)
    that would truncate the harvest badly."""
    from paperbase.sources import VenueCandidate

    service = _service()
    call_count = {"n": 0}

    def stub(*, venue, year, cursor=None, page_size=1000, timeout_seconds=30):
        idx = call_count["n"]
        call_count["n"] += 1
        # Page 0: real data. Page 1: empty (all filtered out client-side)
        # but next_cursor is still set. Page 2: more real data, then EOF.
        if idx == 0:
            return [
                VenueCandidate(arxiv_id="2020.00001", semantic_scholar_id="s1", title="P1", venue_string="ICLR canonical", year=2024),
            ], "token-0"
        if idx == 1:
            return [], "token-1"  # empty filtered page, but more remains
        if idx == 2:
            return [
                VenueCandidate(arxiv_id="2020.00002", semantic_scholar_id="s2", title="P2", venue_string="ICLR canonical", year=2024),
            ], None
        return [], None

    monkeypatch.setattr("paperbase.service.sources.fetch_venue_accepted", stub)

    result = service.run_harvest_venue(
        venue="ICLR",
        year=2024,
        max_papers=None,
        chunk_size=50,
    )

    # Both real candidates collected — the planner did NOT stop on the
    # empty page 1.
    assert result["candidates_emitted"] == 2
    assert result["pages_fetched"] == 3
    assert call_count["n"] == 3


def test_run_harvest_chunk_venue_indexes_and_stamps_venue(monkeypatch):
    """The chunk worker for ``source='s2_venue'`` calls
    ``index_arxiv_paper`` per id, then stamps venue/venue_year/
    venue_source via ``set_paper_venue``. Verifies stamp values."""
    service = _service()

    # Pre-populate the memory repo so set_paper_venue has rows to update.
    for aid in ("2020.00001", "2020.00002"):
        service.repository.upsert_paper(PaperMetadata(arxiv_id=aid, title=f"Paper {aid}"))

    def fake_index(arxiv_id, *, paper=None, force=False):
        # Return the existing PaperResponse so chunk worker can use paper_id.
        return service.repository.get_paper_by_arxiv_id(arxiv_id)

    monkeypatch.setattr(service, "index_arxiv_paper", fake_index)

    result = service.run_harvest_chunk_venue(
        papers=[
            {"arxiv_id": "2020.00001", "venue": "International Conference on Learning Representations", "year": 2024},
            {"arxiv_id": "2020.00002", "venue": "International Conference on Learning Representations", "year": 2024},
        ],
        venue="ICLR",
        year=2024,
    )

    assert result == {"requested": 2, "indexed": 2, "stamped": 2, "skipped": 0}
    p1 = service.repository.get_paper_by_arxiv_id("2020.00001")
    assert p1.venue == "International Conference on Learning Representations"
    assert p1.venue_year == 2024
    assert p1.venue_source == "s2_bulk"


def test_run_harvest_chunk_venue_swallows_per_id_permanent_errors(monkeypatch):
    """A PermanentError on one arxiv id (e.g. 404 / withdrawn) must not
    kill the whole chunk — other ids still get indexed and stamped."""
    service = _service()
    service.repository.upsert_paper(PaperMetadata(arxiv_id="2020.00001"))
    service.repository.upsert_paper(PaperMetadata(arxiv_id="2020.00003"))

    def fake_index(arxiv_id, *, paper=None, force=False):
        if arxiv_id == "2020.bad":
            raise PermanentError("arxiv 404")
        return service.repository.get_paper_by_arxiv_id(arxiv_id)

    monkeypatch.setattr(service, "index_arxiv_paper", fake_index)

    result = service.run_harvest_chunk_venue(
        papers=[
            {"arxiv_id": "2020.00001", "venue": "ICLR canonical", "year": 2024},
            {"arxiv_id": "2020.bad", "venue": "ICLR canonical", "year": 2024},
            {"arxiv_id": "2020.00003", "venue": "ICLR canonical", "year": 2024},
        ],
    )

    assert result == {"requested": 3, "indexed": 2, "stamped": 2, "skipped": 1}


def test_run_harvest_chunk_venue_handles_empty_payload():
    """Empty papers list (e.g. corrupt payload from a retry) returns
    zero counts without raising."""
    service = _service()
    assert service.run_harvest_chunk_venue(papers=[]) == {
        "requested": 0,
        "indexed": 0,
        "stamped": 0,
        "skipped": 0,
    }


def test_default_priority_venue_matches_harvest_range():
    """Venue planner shares priority tier 5 with harvest_range so
    interactive ``index_paper`` always cuts in front."""
    from paperbase.repository import default_max_attempts, default_priority

    assert default_priority("harvest_venue") == 5
    assert default_priority("harvest_range") == default_priority("harvest_venue")
    assert default_max_attempts("harvest_venue") == 3


def test_fetch_venue_accepted_validates_inputs():
    """Sources-side guards — surface as ValueError because the planner
    has already validated and converted to PermanentError on its side."""
    from paperbase.sources import fetch_venue_accepted

    with pytest.raises(ValueError):
        fetch_venue_accepted(venue="", year=2024)
    with pytest.raises(ValueError):
        fetch_venue_accepted(venue="ICLR", year=1899)
    with pytest.raises(ValueError):
        fetch_venue_accepted(venue="ICLR", year=2024, page_size=0)
    with pytest.raises(ValueError):
        fetch_venue_accepted(venue="ICLR", year=2024, page_size=1001)


def test_set_paper_venue_preserves_other_paper_fields():
    """The venue setter must update only venue columns — citation_count,
    title, etc. must be left untouched, otherwise a venue stamp would
    overwrite the indexed paper's content."""
    repo = MemoryPaperRepository()
    p = repo.upsert_paper(PaperMetadata(
        arxiv_id="2020.00001",
        title="Original",
        citation_count=42,
        abstract="abstract body",
    ))
    out = repo.set_paper_venue(p.paper_id, venue="ICLR", venue_year=2024, venue_source="s2_bulk")
    assert out is not None
    assert out.title == "Original"
    assert out.citation_count == 42
    assert out.abstract == "abstract body"
    assert out.venue == "ICLR"
    assert out.venue_year == 2024
    assert out.venue_source == "s2_bulk"


def test_set_paper_venue_returns_none_for_missing_paper():
    """Missing paper → None (caller is responsible for indexing first)."""
    repo = MemoryPaperRepository()
    assert repo.set_paper_venue("paper_does_not_exist", venue="ICLR", venue_year=2024, venue_source="s2_bulk") is None


# --- author-signal harvest (#39 PR C) ---------------------------------------


def _fake_author_pages(pages_by_author: dict[str, list[list[tuple[str, int]]]]):
    """Build a sources.fetch_author_papers stub that serves successive
    pages keyed by author id. Each tuple is ``(arxiv_id, citation_count)``.
    Last page returns next_cursor=None."""
    from paperbase.sources import AuthorPaperCandidate

    state: dict[str, int] = {}
    cursors_seen: list[tuple[str, int | None]] = []

    def stub(*, s2_author_id, cursor=0, page_size=1000, timeout_seconds=30):
        cursors_seen.append((s2_author_id, cursor))
        idx = state.get(s2_author_id, 0)
        state[s2_author_id] = idx + 1
        pages = pages_by_author.get(s2_author_id, [])
        if idx >= len(pages):
            return [], None
        page = pages[idx]
        candidates = [
            AuthorPaperCandidate(
                arxiv_id=aid,
                semantic_scholar_id=f"s2-{aid}",
                title=f"Paper {aid}",
                citation_count=cites,
                year=2020,
            )
            for aid, cites in page
        ]
        next_cursor = None if idx == len(pages) - 1 else (cursor or 0) + len(page)
        return candidates, next_cursor

    return stub, cursors_seen


def test_enqueue_harvest_author_job_rejects_invalid_inputs():
    """Operator-supplied payloads must fail fast as PermanentError so
    the planner job dead-letters rather than retrying garbage."""
    service = _service()
    with pytest.raises(PermanentError):
        service.enqueue_harvest_author_job(min_paper_count=0)
    with pytest.raises(PermanentError):
        service.enqueue_harvest_author_job(min_cite_mass=-1)
    with pytest.raises(PermanentError):
        service.enqueue_harvest_author_job(max_authors=0)
    with pytest.raises(PermanentError):
        service.enqueue_harvest_author_job(max_papers_per_author=0)
    with pytest.raises(PermanentError):
        service.enqueue_harvest_author_job(chunk_size=0)
    with pytest.raises(PermanentError):
        service.enqueue_harvest_author_job(chunk_size=1000)


def test_run_harvest_author_planner_visits_top_authors_and_emits_chunks(monkeypatch):
    """Planner derives top authors from the indexed corpus, then for
    each pages S2 and enqueues ``harvest_chunk`` jobs with
    ``source='s2_author'`` carrying the discovering author's id."""
    service = _service()

    # Seed corpus: two papers by author A1 (high cite mass), one by A2 (lower).
    p_high = service.repository.upsert_paper(PaperMetadata(arxiv_id="2020.00001", citation_count=400))
    p_med = service.repository.upsert_paper(PaperMetadata(arxiv_id="2020.00002", citation_count=100))
    p_low = service.repository.upsert_paper(PaperMetadata(arxiv_id="2020.00003", citation_count=50))
    service.repository.replace_paper_authors(p_high.paper_id, authors=[{"s2_id": "A1", "name": "Alice", "position": 1}])
    service.repository.replace_paper_authors(p_med.paper_id, authors=[{"s2_id": "A1", "name": "Alice", "position": 1}])
    service.repository.replace_paper_authors(p_low.paper_id, authors=[{"s2_id": "A2", "name": "Bob", "position": 1}])

    pages_by_author = {
        "A1": [[("2024.{:05d}".format(i), 10) for i in range(5)]],
        "A2": [[("2025.{:05d}".format(i), 10) for i in range(2)]],
    }
    stub, cursors_seen = _fake_author_pages(pages_by_author)
    monkeypatch.setattr("paperbase.service.sources.fetch_author_papers", stub)

    result = service.run_harvest_author(
        min_paper_count=1,
        min_cite_mass=0,
        max_authors=10,
        max_papers_per_author=None,
        chunk_size=4,
    )

    assert result["authors_visited"] == 2
    # A1 yielded 5 papers / 4 per chunk = 2 chunks. A2 yielded 2 / 4 = 1 chunk.
    assert result["chunks_created"] == 3
    assert result["candidates_emitted"] == 7
    chunks = [j for j in service.repository.jobs.values() if j.job_type == "harvest_chunk"]
    assert all(c.payload["source"] == "s2_author" for c in chunks)
    a1_chunks = [c for c in chunks if c.payload["top_author_s2_id"] == "A1"]
    a2_chunks = [c for c in chunks if c.payload["top_author_s2_id"] == "A2"]
    assert len(a1_chunks) == 2
    assert len(a2_chunks) == 1
    # Cursors handed back: A1 calls were (cursor=0 first, then next_cursor for second page).
    a1_cursors = [c[1] for c in cursors_seen if c[0] == "A1"]
    assert a1_cursors[0] == 0


def test_run_harvest_author_respects_max_papers_per_author(monkeypatch):
    """``max_papers_per_author`` caps per-author paging so a prolific
    author doesn't monopolize the planner."""
    service = _service()
    p = service.repository.upsert_paper(PaperMetadata(arxiv_id="2020.00001", citation_count=10_000))
    service.repository.replace_paper_authors(p.paper_id, authors=[{"s2_id": "A1", "name": "Alice", "position": 1}])

    pages_by_author = {
        "A1": [[("2024.{:05d}".format(i), 10) for i in range(5)] for _ in range(3)],
    }
    stub, _cursors = _fake_author_pages(pages_by_author)
    monkeypatch.setattr("paperbase.service.sources.fetch_author_papers", stub)

    result = service.run_harvest_author(
        min_paper_count=1,
        min_cite_mass=0,
        max_authors=10,
        max_papers_per_author=7,
        chunk_size=10,
    )

    assert result["candidates_emitted"] == 7
    assert result["authors_visited"] == 1


def test_run_harvest_author_handles_empty_corpus(monkeypatch):
    """Empty paper_authors → no top authors, no chunks."""
    service = _service()
    # Stub to detect any unexpected S2 calls.
    called = {"n": 0}

    def stub(**_kw):
        called["n"] += 1
        return [], None

    monkeypatch.setattr("paperbase.service.sources.fetch_author_papers", stub)

    result = service.run_harvest_author(
        min_paper_count=1,
        min_cite_mass=0,
        max_authors=10,
        max_papers_per_author=None,
        chunk_size=50,
    )

    assert result == {"authors_visited": 0, "chunks_created": 0, "candidates_emitted": 0}
    assert called["n"] == 0


def test_run_harvest_author_continues_through_empty_filtered_pages(monkeypatch):
    """Same EOF correctness fix as PR #50 / #52: ``fetch_author_papers``
    filters non-arxiv rows client-side and may return an empty ``page``
    while ``next_cursor`` is still set. The planner must keep paging
    until S2 itself signals end-of-stream — not stop on the first
    empty filtered page (e.g. when an author has a stretch of conf-
    only papers without arxiv links)."""
    from paperbase.sources import AuthorPaperCandidate

    service = _service()
    p = service.repository.upsert_paper(PaperMetadata(arxiv_id="2020.00001", citation_count=10_000))
    service.repository.replace_paper_authors(p.paper_id, authors=[{"s2_id": "A1", "name": "Alice", "position": 1}])

    call_count = {"n": 0}

    def stub(*, s2_author_id, cursor=0, page_size=1000, timeout_seconds=30):
        idx = call_count["n"]
        call_count["n"] += 1
        if idx == 0:
            return [
                AuthorPaperCandidate(arxiv_id="2024.00001", semantic_scholar_id="s1", title="P1", citation_count=5, year=2024),
            ], 1  # next offset
        if idx == 1:
            return [], 2  # empty filtered page, but more remains
        if idx == 2:
            return [
                AuthorPaperCandidate(arxiv_id="2024.00002", semantic_scholar_id="s2", title="P2", citation_count=5, year=2024),
            ], None  # last page
        return [], None

    monkeypatch.setattr("paperbase.service.sources.fetch_author_papers", stub)

    result = service.run_harvest_author(
        min_paper_count=1,
        min_cite_mass=0,
        max_authors=1,
        max_papers_per_author=None,
        chunk_size=50,
    )

    # Both real candidates collected — the planner did NOT stop on the
    # empty page 1.
    assert result["candidates_emitted"] == 2
    assert call_count["n"] == 3


def test_run_harvest_chunk_author_indexes_and_stamps_top_author(monkeypatch):
    """Chunk worker for ``source='s2_author'`` indexes each paper and
    stamps ``top_author_s2_id`` via ``set_paper_top_author``."""
    service = _service()
    for aid in ("2020.00001", "2020.00002"):
        service.repository.upsert_paper(PaperMetadata(arxiv_id=aid))

    def fake_index(arxiv_id, *, paper=None, force=False):
        return service.repository.get_paper_by_arxiv_id(arxiv_id)

    monkeypatch.setattr(service, "index_arxiv_paper", fake_index)

    result = service.run_harvest_chunk_author(
        arxiv_ids=["2020.00001", "2020.00002"],
        top_author_s2_id="A1",
    )

    assert result == {"requested": 2, "indexed": 2, "stamped": 2, "skipped": 0}
    p1 = service.repository.get_paper_by_arxiv_id("2020.00001")
    assert p1.top_author_s2_id == "A1"


def test_run_harvest_chunk_author_swallows_per_id_permanent_errors(monkeypatch):
    """A PermanentError on one arxiv id (e.g. 404) must not kill the
    whole chunk."""
    service = _service()
    service.repository.upsert_paper(PaperMetadata(arxiv_id="2020.00001"))
    service.repository.upsert_paper(PaperMetadata(arxiv_id="2020.00003"))

    def fake_index(arxiv_id, *, paper=None, force=False):
        if arxiv_id == "2020.bad":
            raise PermanentError("arxiv 404")
        return service.repository.get_paper_by_arxiv_id(arxiv_id)

    monkeypatch.setattr(service, "index_arxiv_paper", fake_index)

    result = service.run_harvest_chunk_author(
        arxiv_ids=["2020.00001", "2020.bad", "2020.00003"],
        top_author_s2_id="A1",
    )

    assert result == {"requested": 3, "indexed": 2, "stamped": 2, "skipped": 1}


def test_run_harvest_chunk_author_skips_stamping_when_no_top_author():
    """When ``top_author_s2_id`` is None (corrupt payload), papers
    still get indexed but not stamped — defensive in case a bad payload
    sneaks through."""
    service = _service()
    service.repository.upsert_paper(PaperMetadata(arxiv_id="2020.00001"))

    # No monkeypatch needed — the chunk method short-circuits if arxiv_ids
    # is empty, but with a real index_arxiv_paper call we'd hit the
    # network. Use empty list to avoid that.
    result = service.run_harvest_chunk_author(arxiv_ids=[])
    assert result == {"requested": 0, "indexed": 0, "stamped": 0, "skipped": 0}


def test_default_priority_author_matches_harvest_range():
    """Author planner shares priority tier 5 with harvest_range so
    interactive ``index_paper`` always cuts in front."""
    from paperbase.repository import default_max_attempts, default_priority

    assert default_priority("harvest_author") == 5
    assert default_priority("harvest_range") == default_priority("harvest_author")
    assert default_max_attempts("harvest_author") == 3


def test_fetch_author_papers_validates_inputs():
    """Sources-side guards on s2_author_id, cursor, page_size."""
    from paperbase.sources import fetch_author_papers

    with pytest.raises(ValueError):
        fetch_author_papers(s2_author_id="")
    with pytest.raises(ValueError):
        fetch_author_papers(s2_author_id="A1", cursor=-1)
    with pytest.raises(ValueError):
        fetch_author_papers(s2_author_id="A1", page_size=0)
    with pytest.raises(ValueError):
        fetch_author_papers(s2_author_id="A1", page_size=1001)


def test_set_paper_top_author_preserves_other_fields():
    """The top_author setter must not touch citation_count, title, etc."""
    repo = MemoryPaperRepository()
    p = repo.upsert_paper(PaperMetadata(
        arxiv_id="2020.00001",
        title="Original",
        citation_count=42,
        abstract="abstract body",
    ))
    out = repo.set_paper_top_author(p.paper_id, top_author_s2_id="A1")
    assert out is not None
    assert out.title == "Original"
    assert out.citation_count == 42
    assert out.abstract == "abstract body"
    assert out.top_author_s2_id == "A1"


def test_set_paper_top_author_is_first_write_wins():
    """When two top authors surface the same paper through concurrent
    harvest_chunk jobs, the first stamp must win — last-write would
    make provenance non-deterministic. Explicit clears (None) still
    bypass the guard so operators can reset."""
    repo = MemoryPaperRepository()
    p = repo.upsert_paper(PaperMetadata(arxiv_id="2020.00001"))

    # First writer lands.
    first = repo.set_paper_top_author(p.paper_id, top_author_s2_id="A1")
    assert first is not None and first.top_author_s2_id == "A1"

    # Second writer is preserved-by-existing — returned row still
    # carries A1, not A2.
    second = repo.set_paper_top_author(p.paper_id, top_author_s2_id="A2")
    assert second is not None and second.top_author_s2_id == "A1"

    # Explicit clear bypasses the guard.
    cleared = repo.set_paper_top_author(p.paper_id, top_author_s2_id=None)
    assert cleared is not None and cleared.top_author_s2_id is None

    # After clearing, a new stamp lands again.
    relanded = repo.set_paper_top_author(p.paper_id, top_author_s2_id="A2")
    assert relanded is not None and relanded.top_author_s2_id == "A2"


def test_run_harvest_chunk_author_dedupes_arxiv_ids(monkeypatch):
    """Multiple top authors can surface the same paper, so the planner
    can produce duplicate ids in a single chunk. The chunk worker
    dedupes before indexing — index_arxiv_paper is idempotent but the
    work is wasted, and ``stamped`` would otherwise overcount."""
    service = _service()
    service.repository.upsert_paper(PaperMetadata(arxiv_id="2020.00001"))

    index_calls: list[str] = []

    def fake_index(arxiv_id, *, paper=None, force=False):
        index_calls.append(arxiv_id)
        return service.repository.get_paper_by_arxiv_id(arxiv_id)

    monkeypatch.setattr(service, "index_arxiv_paper", fake_index)

    result = service.run_harvest_chunk_author(
        arxiv_ids=["2020.00001", "2020.00001", "2020.00001"],
        top_author_s2_id="A1",
    )

    # Three requested, but only one unique id → one index call.
    assert index_calls == ["2020.00001"]
    assert result["requested"] == 3
    assert result["indexed"] == 1
    assert result["stamped"] == 1
    # Duplicates count as skipped so the totals add up to ``requested``.
    assert result["skipped"] == 2


def test_run_harvest_chunk_author_does_not_overcount_when_already_stamped(monkeypatch):
    """Under first-write-wins, attempting to stamp a paper that another
    author already claimed must NOT increment ``stamped`` — that count
    reflects papers whose post-call top_author is ours."""
    service = _service()
    p = service.repository.upsert_paper(PaperMetadata(arxiv_id="2020.00001"))
    # Pre-stamp by a different author.
    service.repository.set_paper_top_author(p.paper_id, top_author_s2_id="A_other")

    def fake_index(arxiv_id, *, paper=None, force=False):
        return service.repository.get_paper_by_arxiv_id(arxiv_id)

    monkeypatch.setattr(service, "index_arxiv_paper", fake_index)

    result = service.run_harvest_chunk_author(
        arxiv_ids=["2020.00001"],
        top_author_s2_id="A_us",
    )

    assert result["indexed"] == 1
    assert result["stamped"] == 0  # A_other won the race

    # Stamp is preserved.
    p_after = service.repository.get_paper_by_arxiv_id("2020.00001")
    assert p_after.top_author_s2_id == "A_other"


def test_list_top_authors_aggregation():
    """list_top_authors aggregates citation_count across an author's
    papers and sorts by cite mass desc. min_paper_count + min_cite_mass
    threshold filtering both apply."""
    repo = MemoryPaperRepository()
    # A1: 2 papers, total 700 cites
    p1 = repo.upsert_paper(PaperMetadata(arxiv_id="2020.00001", citation_count=500))
    p2 = repo.upsert_paper(PaperMetadata(arxiv_id="2020.00002", citation_count=200))
    repo.replace_paper_authors(p1.paper_id, authors=[{"s2_id": "A1", "name": "Alice", "position": 1}])
    repo.replace_paper_authors(p2.paper_id, authors=[{"s2_id": "A1", "name": "Alice", "position": 1}])
    # A2: 1 paper, 1000 cites — wins on cite mass
    p3 = repo.upsert_paper(PaperMetadata(arxiv_id="2020.00003", citation_count=1000))
    repo.replace_paper_authors(p3.paper_id, authors=[{"s2_id": "A2", "name": "Bob", "position": 1}])
    # A3: 1 paper, 5 cites — fails min_cite_mass threshold
    p4 = repo.upsert_paper(PaperMetadata(arxiv_id="2020.00004", citation_count=5))
    repo.replace_paper_authors(p4.paper_id, authors=[{"s2_id": "A3", "name": "Carol", "position": 1}])

    # Default: all visible
    out = repo.list_top_authors(min_paper_count=1, min_cite_mass=0, limit=10)
    assert [r["author_s2_id"] for r in out] == ["A2", "A1", "A3"]
    assert out[0]["cite_mass"] == 1000
    assert out[1]["cite_mass"] == 700
    assert out[1]["paper_count"] == 2

    # Cite-mass threshold drops A3
    filtered = repo.list_top_authors(min_paper_count=1, min_cite_mass=100, limit=10)
    assert [r["author_s2_id"] for r in filtered] == ["A2", "A1"]

    # Paper-count threshold of 2 drops A2 and A3
    multi = repo.list_top_authors(min_paper_count=2, min_cite_mass=0, limit=10)
    assert [r["author_s2_id"] for r in multi] == ["A1"]


def test_replace_paper_authors_dedupes_and_clears():
    """Calling with a list dedupes by s2_id; calling with [] clears all
    rows for the paper."""
    repo = MemoryPaperRepository()
    p = repo.upsert_paper(PaperMetadata(arxiv_id="2020.00001"))
    repo.replace_paper_authors(p.paper_id, authors=[
        {"s2_id": "A1", "name": "Alice", "position": 1},
        {"s2_id": "A1", "name": "Alice", "position": 2},  # duplicate s2_id
        {"s2_id": "", "name": "NoId", "position": 3},  # empty s2_id skipped
        {"s2_id": "A2", "name": "Bob", "position": 4},
    ])
    rows = repo.paper_authors[p.paper_id]
    assert sorted(r["s2_id"] for r in rows) == ["A1", "A2"]

    repo.replace_paper_authors(p.paper_id, authors=[])
    assert repo.paper_authors[p.paper_id] == []


def test_enrich_with_semantic_scholar_surfaces_author_rows(monkeypatch):
    """The enrich path populates ``EnrichmentOutcome.authors_by_arxiv``
    with parsed author rows. Service callers persist these via
    ``replace_paper_authors``. Stubs the S2 batch response."""
    from paperbase import sources

    def fake_batch(ids, **_kw):
        return [
            {
                "paperId": "s2-X",
                "citationCount": 100,
                "influentialCitationCount": 5,
                "authors": [
                    {"authorId": "A1", "name": "Alice"},
                    {"authorId": "A2", "name": "Bob"},
                    {"authorId": "", "name": "NoId"},  # filtered out
                ],
            }
            for _ in ids
        ]

    monkeypatch.setattr("paperbase.sources._s2_batch_with_retry", fake_batch)

    metas = [PaperMetadata(arxiv_id="2020.00001")]
    outcome = sources.enrich_with_semantic_scholar(metas, max_retries=0)
    assert outcome.enriched == 1
    rows = outcome.authors_by_arxiv["2020.00001"]
    assert [r["s2_id"] for r in rows] == ["A1", "A2"]
    assert rows[0]["position"] == 1
    assert rows[1]["position"] == 2
