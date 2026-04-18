"""Background worker that processes paperbase jobs and runs scheduled sync."""

from __future__ import annotations

import time

from .config import get_settings
from .service import get_service


def run_forever() -> None:
    settings = get_settings()
    service = get_service()
    last_sync = 0.0
    while True:
        now = time.time()
        if now - last_sync >= settings.sync_interval_seconds:
            service.enqueue_latest_sync_job()
            last_sync = now
        processed = service.process_next_job()
        if processed is None:
            service.cleanup_expired_results()
            time.sleep(settings.worker_poll_interval_seconds)


def main() -> None:
    run_forever()
