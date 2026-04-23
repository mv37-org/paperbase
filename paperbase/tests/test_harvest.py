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
        limit_per_category=None,
        chunk_months=3,
    )

    assert result == {"chunks_created": 4}
    chunk_jobs = [job for job in service.repository.jobs.values() if job.job_type == "harvest_chunk"]
    assert len(chunk_jobs) == 4
    categories = sorted({job.payload["category"] for job in chunk_jobs})
    assert categories == ["cs.CL", "cs.LG"]
    windows = sorted({(job.payload["start_date"], job.payload["end_date"]) for job in chunk_jobs})
    assert windows == [("2020-01-01", "2020-03-31"), ("2020-04-01", "2020-06-30")]


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
            limit_per_category=None,
            chunk_months=3,
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


def test_harvest_chunk_paginates_until_short_page(monkeypatch):
    """The chunk worker must keep fetching until fetch_range returns either
    an empty page or next_cursor=None. Here: 2 full pages of 100, then a
    short page of 5 → 205 papers indexed, exactly 3 fetch_range calls."""
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

    result = service.run_harvest_chunk(
        source="arxiv",
        category="cs.LG",
        start_date="2020-01-01",
        end_date="2020-03-31",
        limit=None,
        cursor=0,
    )

    assert result["fetched"] == 205
    assert result["indexed"] == 205
    assert counters["cs.LG"] == 3
    assert len(indexed_ids) == 205


def test_harvest_chunk_respects_limit(monkeypatch):
    """``limit`` caps total work per chunk. A limit of 50 against a 100-paper
    page must stop mid-page and not fetch a second page."""
    service = _service()
    full_page = [_paper(f"2020.{i:05d}") for i in range(100)]
    fetch_stub, counters = _fake_fetch_range({"cs.LG": [full_page, full_page]})

    monkeypatch.setattr("paperbase.service.sources.fetch_range", fetch_stub)
    monkeypatch.setattr(service, "index_arxiv_paper", lambda *_a, **_k: None)

    result = service.run_harvest_chunk(
        source="arxiv",
        category="cs.LG",
        start_date="2020-01-01",
        end_date="2020-03-31",
        limit=50,
        cursor=0,
    )

    assert result["fetched"] == 50
    assert counters["cs.LG"] == 1, "limit must stop the worker before a second fetch"


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
