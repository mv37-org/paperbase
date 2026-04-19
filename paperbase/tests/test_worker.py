from __future__ import annotations

from types import SimpleNamespace

from paperbase.config import Settings
from paperbase.worker import _initial_last_sync, run_one_iteration


class _StubService:
    def __init__(self) -> None:
        self.enqueued_jobs: list[str] = []
        self.cleanup_calls = 0
        self.processed_jobs: list[object] = []

    def enqueue_latest_sync_job(self):
        job_id = f"job_{len(self.enqueued_jobs) + 1}"
        self.enqueued_jobs.append(job_id)
        return SimpleNamespace(job_id=job_id)

    def process_next_job(self):
        if self.processed_jobs:
            return self.processed_jobs.pop(0)
        return None

    def cleanup_expired_results(self) -> None:
        self.cleanup_calls += 1


def test_initial_last_sync_triggers_immediate_sync_by_default():
    settings = Settings(worker_auto_sync_latest=True, worker_initial_sync_latest=True)

    assert _initial_last_sync(settings, now=123.0) == 0.0


def test_initial_last_sync_skips_immediate_sync_when_disabled():
    settings = Settings(worker_auto_sync_latest=True, worker_initial_sync_latest=False)

    assert _initial_last_sync(settings, now=123.0) == 123.0


def test_run_one_iteration_enqueues_initial_sync_job_when_enabled():
    settings = Settings(
        worker_auto_sync_latest=True,
        worker_initial_sync_latest=True,
        sync_interval_seconds=3600,
        worker_poll_interval_seconds=0,
    )
    service = _StubService()

    updated_last_sync = run_one_iteration(settings=settings, service=service, last_sync=0.0, now=4000.0, sleep_fn=lambda _: None)

    assert service.enqueued_jobs == ["job_1"]
    assert service.cleanup_calls == 1
    assert updated_last_sync == 4000.0


def test_run_one_iteration_does_not_enqueue_sync_when_auto_sync_disabled():
    settings = Settings(
        worker_auto_sync_latest=False,
        worker_initial_sync_latest=False,
        sync_interval_seconds=3600,
        worker_poll_interval_seconds=0,
    )
    service = _StubService()

    updated_last_sync = run_one_iteration(settings=settings, service=service, last_sync=100.0, now=200.0, sleep_fn=lambda _: None)

    assert service.enqueued_jobs == []
    assert service.cleanup_calls == 1
    assert updated_last_sync == 100.0
