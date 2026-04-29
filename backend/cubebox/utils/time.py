"""Timezone-safe datetime utilities."""

from datetime import UTC, datetime


def utc_isoformat(dt: datetime) -> str:
    """Return an ISO 8601 string that always includes the UTC offset.

    `timestamp without time zone` columns strip tz info, so datetimes read back
    from the DB are naive.  This helper attaches UTC before formatting so the
    frontend can unambiguously parse the timestamp.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat()
