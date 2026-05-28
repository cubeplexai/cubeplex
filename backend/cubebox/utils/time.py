"""Timezone-safe datetime utilities."""

from datetime import datetime


def utc_isoformat(dt: datetime) -> str:
    """Return an ISO 8601 string with the UTC offset.

    Post-timestamptz-migration, every datetime in cubebox is tz-aware by
    construction. A naive dt reaching this helper means someone violated
    the hard rule -- fail loudly so the bug is visible rather than silently
    fixed.
    """
    assert dt.tzinfo is not None, (
        f"naive datetime reached utc_isoformat: {dt!r}; should be tz-aware"
    )
    return dt.isoformat()
