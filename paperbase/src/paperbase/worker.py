"""Background worker that processes paperbase jobs and runs scheduled sync."""

from __future__ import annotations

import logging
import time

from .config import Settings, get_settings
from .logging_utils import configure_logging
from .service import PaperbaseService, get_service


logger = logging.getLogger(__name__)


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
    if processed is None:
        service.cleanup_expired_results()
        logger.debug(
            "Worker idle",
            extra={
                "event": "worker.idle",
                "sleep_seconds": settings.worker_poll_interval_seconds,
            },
        )
        sleep_fn(settings.worker_poll_interval_seconds)
        return last_sync

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


def run_forever() -> None:
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
        last_sync = run_one_iteration(settings=settings, service=service, last_sync=last_sync)


def main() -> None:
    run_forever()
