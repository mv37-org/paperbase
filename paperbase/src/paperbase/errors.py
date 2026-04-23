"""Failure-classification markers for the worker retry policy (#32).

Job handlers raise ``RetryableError`` for transient failures (network
timeouts, 5xx, rate limits). The worker requeues with exponential
backoff + jitter up to the per-job-type ``max_attempts`` budget, then
dead-letters.

They raise ``PermanentError`` for validation / 4xx / structural problems
(missing paper, malformed payload, configuration error). The worker
dead-letters immediately regardless of attempts.

Uncaught exceptions default to retryable — safer to retry a bug than
silently drop work. Explicit is better than heuristic.
"""

from __future__ import annotations


class JobError(Exception):
    """Base class for worker-aware job errors."""


class RetryableError(JobError):
    """Transient failure. Worker requeues with backoff until
    ``max_attempts`` is reached, then promotes to ``dead``."""


class PermanentError(JobError):
    """Structural failure. Worker skips retry and promotes to ``dead``
    immediately. Raise this when re-running would never succeed —
    e.g. arxiv 404, invalid payload, disabled feature."""
