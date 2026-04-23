"""Rate-limit primitives for upstream API calls.

arXiv's etiquette is one request per three seconds (https://info.arxiv.org/help/api/tou.html).
Exceeding it gets our IP throttled or blocked. The token bucket here gates every
arXiv call behind the same policy so bulk backfill paths (``fetch_range``) cannot
starve the regular ``fetch_recent`` hourly sync nor get us banned.

Thread-safe via a single lock around bucket state; the bucket is refilled
lazily on each acquire so there's no background thread to manage.
"""

from __future__ import annotations

import threading
import time


class TokenBucketRateLimiter:
    """Classic token-bucket limiter.

    ``rate_per_second`` tokens are added to the bucket every second up to
    ``burst`` tokens. ``acquire()`` blocks until one token is available.

    Used by the module-level ``ARXIV_LIMITER``. Not intended to be constructed
    ad-hoc in hot paths; construct once at import time and share.
    """

    def __init__(self, *, rate_per_second: float, burst: int) -> None:
        if rate_per_second <= 0:
            raise ValueError("rate_per_second must be positive")
        if burst < 1:
            raise ValueError("burst must be at least 1")
        self._rate = float(rate_per_second)
        self._capacity = float(burst)
        self._tokens = float(burst)
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()
        self._now = time.monotonic
        self._sleep = time.sleep

    def _refill_locked(self) -> None:
        now = self._now()
        elapsed = now - self._last_refill
        if elapsed <= 0:
            return
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        self._last_refill = now

    def acquire(self) -> None:
        """Block until one token is available, then consume it."""
        while True:
            with self._lock:
                self._refill_locked()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                # How long until the bucket has one full token?
                deficit = 1.0 - self._tokens
                wait = deficit / self._rate
            self._sleep(max(wait, 0.001))


# Module-level limiter for arXiv API. All arxiv-targeted call sites in
# ``sources`` go through this, keeping the ingest-time and backfill paths
# under one budget.
ARXIV_LIMITER = TokenBucketRateLimiter(rate_per_second=1.0 / 3.0, burst=3)
