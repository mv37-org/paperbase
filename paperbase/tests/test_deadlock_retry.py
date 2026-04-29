"""Unit tests for the repository-layer deadlock-retry helper (#51)."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.exc import OperationalError

from paperbase.repository import _is_deadlock_error, _retry_on_deadlock


class _FakePsycopgError:
    """Stand-in for psycopg's exception object on ``OperationalError.orig``.

    Real psycopg errors expose ``sqlstate`` as a string; we mimic that
    so ``_is_deadlock_error`` can inspect the SQLSTATE without needing
    a live database connection.
    """

    def __init__(self, sqlstate: str) -> None:
        self.sqlstate = sqlstate


def _make_operational(sqlstate: str) -> OperationalError:
    err = OperationalError("statement", {}, _FakePsycopgError(sqlstate))
    return err


def test_is_deadlock_error_matches_sqlstate_40p01():
    """Postgres signals deadlock with SQLSTATE 40P01; nothing else
    should match the helper's deadlock predicate."""
    assert _is_deadlock_error(_make_operational("40P01")) is True
    assert _is_deadlock_error(_make_operational("23505")) is False  # unique violation
    assert _is_deadlock_error(_make_operational("08006")) is False  # connection failure


def test_retry_on_deadlock_passes_through_on_success():
    """No retry, no sleep, just returns the value."""
    sleeps: list[float] = []

    def _fn() -> int:
        return 42

    result = _retry_on_deadlock(_fn, sleep_fn=sleeps.append)
    assert result == 42
    assert sleeps == []


def test_retry_on_deadlock_retries_after_one_deadlock_then_succeeds():
    """First call raises deadlock; second call returns. Helper sleeps
    once with the configured backoff and returns the second result."""
    calls = {"n": 0}
    sleeps: list[float] = []

    def _fn() -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise _make_operational("40P01")
        return "ok"

    result = _retry_on_deadlock(_fn, base_backoff_seconds=0.1, sleep_fn=sleeps.append)
    assert result == "ok"
    assert calls["n"] == 2
    assert sleeps == [0.1]


def test_retry_on_deadlock_uses_exponential_backoff():
    """Successive deadlocks back off with 2^n base. Verifies the cadence
    so an operator reading logs sees a predictable sleep pattern."""
    calls = {"n": 0}
    sleeps: list[float] = []

    def _fn() -> str:
        calls["n"] += 1
        if calls["n"] < 4:
            raise _make_operational("40P01")
        return "ok"

    result = _retry_on_deadlock(
        _fn, max_attempts=4, base_backoff_seconds=0.05, sleep_fn=sleeps.append,
    )
    assert result == "ok"
    # 3 retries → 3 sleeps. base * 2**0, base * 2**1, base * 2**2.
    assert sleeps == [0.05, 0.10, 0.20]


def test_retry_on_deadlock_raises_after_max_attempts():
    """If every attempt deadlocks, the final OperationalError surfaces
    so the caller (or job retry policy) can dead-letter / requeue."""
    calls = {"n": 0}
    sleeps: list[float] = []

    def _fn() -> Any:
        calls["n"] += 1
        raise _make_operational("40P01")

    with pytest.raises(OperationalError):
        _retry_on_deadlock(_fn, max_attempts=3, sleep_fn=sleeps.append)
    assert calls["n"] == 3
    # Sleeps only happen between attempts, so 3 attempts → 2 sleeps.
    assert len(sleeps) == 2


def test_retry_on_deadlock_does_not_retry_non_deadlock_error():
    """A different OperationalError (e.g. unique-constraint violation
    surfaced as 23505, or a connection error) should propagate
    immediately — retrying those would mask real bugs."""
    calls = {"n": 0}
    sleeps: list[float] = []

    def _fn() -> Any:
        calls["n"] += 1
        raise _make_operational("23505")  # unique violation, not deadlock

    with pytest.raises(OperationalError):
        _retry_on_deadlock(_fn, sleep_fn=sleeps.append)
    assert calls["n"] == 1
    assert sleeps == []
