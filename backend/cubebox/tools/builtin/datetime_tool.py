"""DateTime Tool

Provides current date and time information.
The model should use this tool instead of guessing the current date/time
from training data.
"""

from datetime import UTC, datetime

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field


class DateTimeInput(BaseModel):
    """Input schema for datetime tool"""

    include_time: bool = Field(
        default=False,
        description="Whether to include the current time (HH:MM:SS). "
        "Default is False, which returns only the date and day of week.",
    )


def get_datetime(include_time: bool = False) -> str:
    """
    Get the current date and optionally time.

    Args:
        include_time: If True, include hours/minutes/seconds in the result.

    Returns:
        Current date string with day of week, and optionally time.
    """
    now = datetime.now(UTC)

    if include_time:
        return now.strftime("%Y-%m-%d %A %H:%M:%S UTC")
    else:
        return now.strftime("%Y-%m-%d %A")


def create_datetime_tool() -> StructuredTool:
    """
    Create a StructuredTool for getting current date/time.

    Returns:
        StructuredTool instance for datetime
    """
    return StructuredTool.from_function(
        func=get_datetime,
        name="datetime",
        description="Get the current date and day of week. "
        "Optionally include the current time. "
        "Use this tool whenever you need to know the current date or time — "
        "do not guess or rely on training data.",
        args_schema=DateTimeInput,
    )
