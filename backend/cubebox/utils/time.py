"""Timezone-safe datetime utilities."""

from datetime import UTC, datetime


def utc_isoformat(dt: datetime) -> str:
    """Return an ISO 8601 string that always includes the UTC offset.

    MySQL/MariaDB DATETIME columns strip timezone info, so datetimes read back
    from the DB are naive.  This helper attaches UTC before formatting so the
    frontend can unambiguously parse the timestamp.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat()
