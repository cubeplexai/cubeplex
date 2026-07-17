"""Pure schedule arithmetic: next-fire, latest-due catch-up, missed-run decision.

Cron is evaluated in the task's IANA timezone; all returned datetimes are UTC.
No DB, no I/O — these are the only unit-tested pieces of the feature.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import cast
from zoneinfo import ZoneInfo

from croniter import croniter


class MissedDecision(StrEnum):
    FIRE = "fire"
    SKIP_MISSED = "skip_missed"


def as_utc(dt: datetime) -> datetime:
    """Attach UTC to a naive datetime; pass through aware ones.

    cubeplex stores `timestamp without time zone`, so datetimes read back from
    the DB are NAIVE. The compute/poller arithmetic mixes those with
    `datetime.now(UTC)` (AWARE); comparing or subtracting the two raises
    TypeError. Every datetime that came from the DB MUST pass through this
    before any comparison/subtraction here.
    """
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def next_fire_after(
    *,
    kind: str,
    after: datetime,
    cron_expr: str | None = None,
    interval_seconds: int | None = None,
    tz: str = "UTC",
) -> datetime:
    """Return the first occurrence strictly after ``after`` (UTC)."""
    after = as_utc(after)
    if kind == "interval":
        if interval_seconds is None or interval_seconds < 60:
            raise ValueError("interval_seconds must be >= 60")
        return after + timedelta(seconds=interval_seconds)
    if kind == "cron":
        if cron_expr is None:
            raise ValueError("cron_expr required for cron schedule")
        zone = ZoneInfo(tz)
        base = after.astimezone(zone)
        nxt = cast(datetime, croniter(cron_expr, base).get_next(datetime))
        return nxt.astimezone(UTC)
    raise ValueError(f"next_fire_after not defined for kind={kind!r}")


def latest_due_before(
    *,
    kind: str,
    candidate: datetime,
    now: datetime,
    cron_expr: str | None = None,
    interval_seconds: int | None = None,
    tz: str = "UTC",
) -> datetime:
    """The latest scheduled occurrence <= now, starting from ``candidate``."""
    candidate = as_utc(candidate)
    now = as_utc(now)
    if candidate > now:
        return candidate
    if kind == "interval":
        if interval_seconds is None or interval_seconds < 60:
            raise ValueError("interval_seconds must be >= 60")
        elapsed = int((now - candidate).total_seconds())
        steps = elapsed // interval_seconds
        return candidate + timedelta(seconds=steps * interval_seconds)
    if kind == "cron":
        if cron_expr is None:
            raise ValueError("cron_expr required for cron schedule")
        zone = ZoneInfo(tz)
        # O(1) catch-up via croniter.get_prev: jump directly to the latest
        # match <= now, instead of walking from candidate one step at a time.
        # A minutely cron after a month of downtime previously took ~43k
        # iterations inside the poller transaction (and inside the resume
        # route handler), enough to stall a poller batch or a request.
        # croniter.get_prev returns the latest match strictly < base, so
        # base off now + 1 microsecond to include now itself when now is
        # an exact cron match.
        zoned_base = (now + timedelta(microseconds=1)).astimezone(zone)
        prev = cast(datetime, croniter(cron_expr, zoned_base).get_prev(datetime))
        prev_utc = prev.astimezone(UTC)
        # The catch-up policy fires the latest match in [candidate, now];
        # if get_prev returned a time before candidate, candidate itself is
        # the latest still-due occurrence.
        return prev_utc if prev_utc >= candidate else candidate
    raise ValueError(f"latest_due_before not defined for kind={kind!r}")


def decide_missed(*, latest_due: datetime, now: datetime, grace_seconds: int) -> MissedDecision:
    """Fire the latest due occurrence if within grace, else skip it as missed."""
    now, latest_due = as_utc(now), as_utc(latest_due)
    if (now - latest_due).total_seconds() <= grace_seconds:
        return MissedDecision.FIRE
    return MissedDecision.SKIP_MISSED
