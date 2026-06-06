"""DateTime tool, declared via ``@cubepi.tool``."""

from __future__ import annotations

from datetime import UTC, datetime

from cubepi import tool
from pydantic import Field


@tool(
    name="datetime",
    description=(
        "Get the current date and day of week. "
        "Optionally include the current time. "
        "Use this tool whenever you need to know the current date or time — "
        "do not guess or rely on training data."
    ),
)
async def datetime_tool(
    include_time: bool = Field(
        default=False,
        description=(
            "Whether to include the current time (HH:MM:SS). "
            "Default is False, which returns only the date and day of week."
        ),
    ),
) -> str:
    now = datetime.now(UTC)
    if include_time:
        return now.strftime("%Y-%m-%d %A %H:%M:%S UTC")
    return now.strftime("%Y-%m-%d %A")
