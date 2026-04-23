from __future__ import annotations

from datetime import date

import pytest

from paperbase.config import Settings
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
