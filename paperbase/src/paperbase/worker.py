"""Background worker that processes paperbase jobs and runs scheduled sync.

The worker ships in two shapes:

- **Standalone loop** (local dev, Cloud Run Jobs): run ``paperbase-worker``
  with no ``PORT`` env set. The process blocks on ``run_forever()`` and exits
  only on signal.
- **HTTP-guarded service** (always-on Cloud Run): set ``PORT`` (Cloud Run does
  this automatically). The worker spawns a daemon thread that runs the job
  loop, and the main thread serves a small FastAPI app with ``/health`` and
  ``/metrics``. Cloud Run's HTTP health check keeps the container alive.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from .config import Settings, get_settings
from .logging_utils import configure_logging
from .service import PaperbaseService, get_service


logger = logging.getLogger(__name__)


@dataclass
class WorkerState:
    """Lightweight in-process counters exposed via ``/metrics``."""

    started_at: float = field(default_factory=time.time)
    last_loop_at: float = 0.0
    processed_count: int = 0
    failed_count: int = 0
    idle_count: int = 0
    last_job_id: str | None = None
    last_job_type: str | None = None
    last_job_status: str | None = None
    last_error: str | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "uptime_seconds": round(time.time() - self.started_at, 2),
            "last_loop_at": self.last_loop_at,
            "processed_count": self.processed_count,
            "failed_count": self.failed_count,
            "idle_count": self.idle_count,
            "last_job_id": self.last_job_id,
            "last_job_type": self.last_job_type,
            "last_job_status": self.last_job_status,
            "last_error": self.last_error,
        }


def _initial_last_sync(settings: Settings, *, now: float | None = None) -> float:
    current_time = time.time() if now is None else now
    if settings.worker_auto_sync_latest and settings.worker_initial_sync_latest:
        return 0.0
    return current_time


def run_one_iteration(
    *,
    settings: Settings,
    service: PaperbaseService,
    last_sync: float,
    now: float | None = None,
    sleep_fn=time.sleep,
    state: WorkerState | None = None,
) -> float:
    current_time = time.time() if now is None else now
    if settings.worker_auto_sync_latest and current_time - last_sync >= settings.sync_interval_seconds:
        job = service.enqueue_latest_sync_job()
        logger.info(
            "Enqueued latest sync job",
            extra={
                "event": "worker.latest_sync_enqueued",
                "job_id": job.job_id,
                "sync_days": settings.latest_sync_days,
                "limit_per_category": settings.latest_sync_limit_per_category,
                "category_count": len(settings.arxiv_categories),
            },
        )
        last_sync = current_time

    processed = service.process_next_job()
    if state is not None:
        state.last_loop_at = time.time()
    if processed is None:
        service.cleanup_expired_results()
        if state is not None:
            state.idle_count += 1
        logger.debug(
            "Worker idle",
            extra={
                "event": "worker.idle",
                "sleep_seconds": settings.worker_poll_interval_seconds,
            },
        )
        sleep_fn(settings.worker_poll_interval_seconds)
        return last_sync

    if state is not None:
        state.processed_count += 1
        state.last_job_id = processed.job_id
        state.last_job_type = processed.job_type
        state.last_job_status = processed.status
        state.last_error = processed.error
        if processed.status == "failed":
            state.failed_count += 1

    logger.info(
        "Processed job",
        extra={
            "event": "worker.job_processed",
            "job_id": processed.job_id,
            "job_type": processed.job_type,
            "job_status": processed.status,
        },
    )
    return last_sync


def run_forever(*, state: WorkerState | None = None) -> None:
    settings = get_settings()
    configure_logging(component="worker", level=settings.log_level, log_format=settings.log_format)
    service = get_service()
    logger.info(
        "Worker starting",
        extra={
            "event": "worker.start",
            "auto_sync_latest": settings.worker_auto_sync_latest,
            "initial_sync_latest": settings.worker_initial_sync_latest,
            "sync_interval_seconds": settings.sync_interval_seconds,
            "poll_interval_seconds": settings.worker_poll_interval_seconds,
        },
    )
    last_sync = _initial_last_sync(settings)
    while True:
        last_sync = run_one_iteration(
            settings=settings,
            service=service,
            last_sync=last_sync,
            state=state,
        )


def _build_http_app(state: WorkerState):
    """FastAPI sidecar so Cloud Run's HTTP healthcheck can keep us alive."""
    from fastapi import FastAPI

    app = FastAPI(title="paperbase-worker", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, str]:
        # Fail the check if the loop thread hasn't ticked in a while — that's
        # a better signal than "process alive" when SPECTER2 loading stalls.
        if state.last_loop_at and time.time() - state.last_loop_at > 600:
            return {"status": "stalled"}
        return {"status": "ok"}

    @app.get("/metrics")
    def metrics() -> dict[str, Any]:
        return state.snapshot()

    return app


def main() -> None:
    port_env = os.environ.get("PORT")
    if not port_env:
        # Standalone loop: Cloud Run Jobs, local dev, Kubernetes with its own
        # probes. Exactly the pre-sidecar behavior.
        run_forever()
        return

    import uvicorn

    state = WorkerState()
    app = _build_http_app(state)

    loop_thread = threading.Thread(
        target=run_forever,
        kwargs={"state": state},
        name="paperbase-worker-loop",
        daemon=True,
    )
    loop_thread.start()

    host = os.environ.get("PAPERBASE_WORKER_HOST", "0.0.0.0")
    port = int(port_env)
    # Re-apply the project's logging config for the uvicorn workers so HTTP
    # access logs inherit the structured format.
    settings = get_settings()
    from .logging_utils import build_log_config

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_config=build_log_config(component="worker-http", level=settings.log_level, log_format=settings.log_format),
        log_level=settings.log_level.lower(),
    )
