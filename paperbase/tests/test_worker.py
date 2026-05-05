from __future__ import annotations
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from paperbase.config import Settings
from paperbase import worker as worker_module
from paperbase.worker import WorkerState, _build_http_app, _initial_last_sync, run_forever, run_one_iteration


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


def test_run_one_iteration_does_not_enqueue_sync_before_interval():
    settings = Settings(
        worker_auto_sync_latest=True,
        worker_initial_sync_latest=False,
        sync_interval_seconds=3600,
        worker_poll_interval_seconds=0,
    )
    service = _StubService()

    updated_last_sync = run_one_iteration(settings=settings, service=service, last_sync=100.0, now=200.0, sleep_fn=lambda _: None)

    assert service.enqueued_jobs == []
    assert service.cleanup_calls == 1
    assert updated_last_sync == 100.0


def test_run_one_iteration_updates_state_for_completed_jobs():
    settings = Settings(
        worker_auto_sync_latest=False,
        worker_initial_sync_latest=False,
        worker_poll_interval_seconds=0,
    )
    service = _StubService()
    service.processed_jobs.append(
        SimpleNamespace(job_id="job_done", job_type="index_paper", status="completed", error=None)
    )
    state = WorkerState(started_at=50.0)

    updated_last_sync = run_one_iteration(
        settings=settings,
        service=service,
        last_sync=25.0,
        now=100.0,
        sleep_fn=lambda _: None,
        state=state,
    )

    assert updated_last_sync == 25.0
    assert state.processed_count == 1
    assert state.completed_count == 1
    assert state.retry_scheduled_count == 0
    assert state.dead_count == 0
    assert state.canceled_count == 0
    assert state.last_job_id == "job_done"
    assert state.last_job_type == "index_paper"
    assert state.last_job_status == "completed"
    assert state.last_error is None
    assert state.last_terminal_error is None
    assert state.last_loop_at > 0


def test_run_one_iteration_counts_retry_scheduled_and_dead_separately():
    """After the #32 retry policy, ``process_next_job`` returns a
    ``JobResponse`` whose status is one of ``completed`` / ``pending``
    (transient retry scheduled) / ``dead`` (terminal failure) /
    ``canceled`` (operator cancellation). The worker must track those
    separately so /metrics can distinguish retry churn from real dead-letter
    growth, and ``last_terminal_error`` only
    surfaces errors that actually dead-lettered.
    """
    settings = Settings(
        worker_auto_sync_latest=False,
        worker_initial_sync_latest=False,
        worker_poll_interval_seconds=0,
    )
    service = _StubService()
    service.processed_jobs.extend(
        [
            SimpleNamespace(job_id="j1", job_type="index_paper", status="pending", error="upstream 503"),
            SimpleNamespace(job_id="j2", job_type="index_paper", status="dead", error="arxiv 404"),
            SimpleNamespace(job_id="j3", job_type="index_paper", status="completed", error=None),
            SimpleNamespace(job_id="j4", job_type="index_paper", status="canceled", error=None),
        ]
    )
    state = WorkerState()

    for _ in range(4):
        run_one_iteration(
            settings=settings,
            service=service,
            last_sync=0.0,
            now=100.0,
            sleep_fn=lambda _: None,
            state=state,
        )

    assert state.processed_count == 4
    assert state.retry_scheduled_count == 1
    assert state.dead_count == 1
    assert state.completed_count == 1
    assert state.canceled_count == 1
    # last_terminal_error should only pick up dead-lettered errors, not
    # transient ones that were requeued for retry.
    assert state.last_terminal_error == "arxiv 404"
    # last_error tracks the most recent job regardless of outcome.
    assert state.last_error is None


def test_run_one_iteration_marks_idle_and_sleeps():
    settings = Settings(
        worker_auto_sync_latest=False,
        worker_initial_sync_latest=False,
        worker_poll_interval_seconds=7,
    )
    service = _StubService()
    state = WorkerState()
    sleeps: list[int] = []

    run_one_iteration(
        settings=settings,
        service=service,
        last_sync=0.0,
        now=10.0,
        sleep_fn=lambda seconds: sleeps.append(seconds),
        state=state,
    )

    assert service.cleanup_calls == 1
    assert state.idle_count == 1
    assert state.processed_count == 0
    assert sleeps == [7]


def test_http_health_reports_stalled_when_loop_is_old(monkeypatch):
    state = WorkerState(started_at=100.0, last_loop_at=200.0)
    app = _build_http_app(state)
    client = TestClient(app)
    monkeypatch.setattr(worker_module.time, "time", lambda: 900.1)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "stalled"}


def test_http_metrics_returns_worker_snapshot(monkeypatch):
    state = WorkerState(
        started_at=100.0,
        last_loop_at=125.0,
        processed_count=3,
        completed_count=2,
        retry_scheduled_count=0,
        dead_count=1,
        canceled_count=1,
        idle_count=2,
        last_job_id="job_123",
        last_job_type="sync_latest",
        last_job_status="dead",
        last_error="arxiv 404",
        last_terminal_error="arxiv 404",
    )
    app = _build_http_app(state)
    client = TestClient(app)
    monkeypatch.setattr(worker_module.time, "time", lambda: 160.0)

    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.json() == {
        "uptime_seconds": 60.0,
        "last_loop_at": 125.0,
        "processed_count": 3,
        "completed_count": 2,
        "retry_scheduled_count": 0,
        "dead_count": 1,
        "canceled_count": 1,
        "idle_count": 2,
        "last_job_id": "job_123",
        "last_job_type": "sync_latest",
        "last_job_status": "dead",
        "last_error": "arxiv 404",
        "last_terminal_error": "arxiv 404",
    }


def test_run_forever_bootstraps_logging_and_service(monkeypatch):
    settings = Settings(
        worker_auto_sync_latest=True,
        worker_initial_sync_latest=True,
        worker_poll_interval_seconds=0,
        log_level="DEBUG",
        log_format="json",
    )
    calls: dict[str, object] = {}
    service = _StubService()

    monkeypatch.setattr(worker_module, "get_settings", lambda: settings)
    monkeypatch.setattr(
        worker_module,
        "configure_logging",
        lambda *, component, level, log_format: calls.update(
            {"component": component, "level": level, "log_format": log_format}
        ),
    )
    monkeypatch.setattr(worker_module, "get_service", lambda: service)

    def stop_after_one_iteration(**kwargs):
        calls["iteration"] = kwargs
        raise RuntimeError("stop-loop")

    monkeypatch.setattr(worker_module, "run_one_iteration", stop_after_one_iteration)

    with pytest.raises(RuntimeError, match="stop-loop"):
        run_forever()

    assert calls["component"] == "worker"
    assert calls["level"] == "DEBUG"
    assert calls["log_format"] == "json"
    assert calls["iteration"]["service"] is service
    assert calls["iteration"]["last_sync"] == 0.0


def test_main_runs_standalone_without_port(monkeypatch):
    monkeypatch.delenv("PORT", raising=False)
    calls: list[str] = []
    monkeypatch.setattr(worker_module, "run_forever", lambda state=None: calls.append("run_forever"))

    worker_module.main()

    assert calls == ["run_forever"]


def test_main_runs_http_sidecar_when_port_is_set(monkeypatch):
    class FakeThread:
        instances: list["FakeThread"] = []

        def __init__(self, *, target, kwargs, name, daemon):
            self.target = target
            self.kwargs = kwargs
            self.name = name
            self.daemon = daemon
            self.started = False
            FakeThread.instances.append(self)

        def start(self):
            self.started = True

    captured: dict[str, object] = {}

    monkeypatch.setenv("PORT", "8123")
    monkeypatch.setenv("PAPERBASE_WORKER_HOST", "127.0.0.7")
    monkeypatch.setattr(worker_module.threading, "Thread", FakeThread)
    monkeypatch.setattr(
        "paperbase.logging_utils.build_log_config",
        lambda *, component, level, log_format: {
            "component": component,
            "level": level,
            "format": log_format,
        },
    )
    monkeypatch.setattr(worker_module, "get_settings", lambda: Settings(log_level="WARNING", log_format="json"))

    def fake_uvicorn_run(app, *, host, port, log_config, log_level):
        captured["app"] = app
        captured["host"] = host
        captured["port"] = port
        captured["log_config"] = log_config
        captured["log_level"] = log_level

    monkeypatch.setattr("uvicorn.run", fake_uvicorn_run)

    worker_module.main()

    assert len(FakeThread.instances) == 1
    thread = FakeThread.instances[0]
    assert thread.started is True
    assert thread.target is worker_module.run_forever
    assert isinstance(thread.kwargs["state"], WorkerState)
    assert thread.name == "paperbase-worker-loop"
    assert thread.daemon is True
    assert captured["host"] == "127.0.0.7"
    assert captured["port"] == 8123
    assert captured["log_level"] == "warning"
