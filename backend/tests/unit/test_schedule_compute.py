from datetime import UTC, datetime, timedelta

import pytest

from cubeplex.schedules.compute import (
    MissedDecision,
    decide_missed,
    latest_due_before,
    next_fire_after,
)

pytestmark = pytest.mark.unit


def _dt(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


def test_interval_next_fire() -> None:
    anchor = _dt(2026, 1, 1, 9, 0)
    assert next_fire_after(
        kind="interval", interval_seconds=3600, after=anchor, tz="UTC"
    ) == anchor + timedelta(seconds=3600)


def test_cron_next_fire_weekday_9am_utc() -> None:
    after = _dt(2026, 1, 2, 9, 0)
    nxt = next_fire_after(kind="cron", cron_expr="0 9 * * 1-5", after=after, tz="UTC")
    assert nxt == _dt(2026, 1, 5, 9, 0)


def test_cron_evaluated_in_task_timezone() -> None:
    after = _dt(2026, 1, 1, 0, 0)
    nxt = next_fire_after(kind="cron", cron_expr="0 9 * * *", after=after, tz="America/New_York")
    assert nxt == _dt(2026, 1, 1, 14, 0)


def test_latest_due_before_hourly_picks_most_recent_not_first() -> None:
    last_next = _dt(2026, 1, 1, 8, 0)
    now = _dt(2026, 1, 1, 10, 2)
    latest = latest_due_before(
        kind="interval", interval_seconds=3600, candidate=last_next, now=now, tz="UTC"
    )
    assert latest == _dt(2026, 1, 1, 10, 0)


def test_decide_missed_within_grace_fires_latest() -> None:
    now = _dt(2026, 1, 1, 10, 2)
    latest = _dt(2026, 1, 1, 10, 0)
    d = decide_missed(latest_due=latest, now=now, grace_seconds=300)
    assert d == MissedDecision.FIRE


def test_decide_missed_past_grace_skips() -> None:
    now = _dt(2026, 1, 1, 10, 10)
    latest = _dt(2026, 1, 1, 10, 0)
    d = decide_missed(latest_due=latest, now=now, grace_seconds=300)
    assert d == MissedDecision.SKIP_MISSED


def test_cron_latest_due_far_overdue_is_constant_time() -> None:
    """Regression for codex P2: catch-up after a long downtime / paused
    stretch must not walk one cron occurrence at a time. A minutely cron
    overdue by ~1 month previously iterated ~43k times inside the poller
    transaction; the get_prev-based path must return in milliseconds.
    """
    import time as _time

    candidate = _dt(2026, 1, 1, 0, 0)
    now = _dt(2026, 2, 1, 0, 0)  # 31 days later
    start = _time.perf_counter()
    latest = latest_due_before(
        kind="cron", cron_expr="* * * * *", candidate=candidate, now=now, tz="UTC"
    )
    elapsed = _time.perf_counter() - start
    # Latest minutely match <= 2026-02-01 00:00 is 2026-02-01 00:00 itself.
    assert latest == now
    # Generous bound — the old O(N) walk took multiple seconds; the
    # get_prev path is a single croniter call (well under 100ms even on CI).
    assert elapsed < 0.5, f"cron catch-up took {elapsed:.3f}s (expected <0.5s)"


def test_cron_latest_due_returns_candidate_when_no_match_in_range() -> None:
    # Weekday-only cron, candidate is a Friday 09:00, now is Saturday 10:00.
    # No weekday match between candidate (exclusive of strictly-prior) and now,
    # so candidate itself is the latest still-due occurrence.
    candidate = _dt(2026, 1, 2, 9, 0)  # Friday 09:00
    now = _dt(2026, 1, 3, 10, 0)  # Saturday 10:00
    latest = latest_due_before(
        kind="cron", cron_expr="0 9 * * 1-5", candidate=candidate, now=now, tz="UTC"
    )
    assert latest == candidate


def test_compute_accepts_naive_db_datetimes() -> None:
    naive_candidate = datetime(2026, 1, 1, 8, 0)
    aware_now = _dt(2026, 1, 1, 10, 2)
    latest = latest_due_before(
        kind="interval",
        interval_seconds=3600,
        candidate=naive_candidate,
        now=aware_now,
        tz="UTC",
    )
    assert latest == _dt(2026, 1, 1, 10, 0)
    assert decide_missed(latest_due=latest, now=aware_now, grace_seconds=300) == (
        MissedDecision.FIRE
    )
    assert next_fire_after(
        kind="interval", interval_seconds=3600, after=datetime(2026, 1, 1, 9, 0), tz="UTC"
    ) == _dt(2026, 1, 1, 10, 0)
