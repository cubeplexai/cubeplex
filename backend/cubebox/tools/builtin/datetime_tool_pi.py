"""DateTime tool ported to cubepi.AgentTool (M2.1)."""

from __future__ import annotations

from cubepi.agent.types import AgentTool, AgentToolResult
from cubepi.providers.base import TextContent
from pydantic import BaseModel, Field

from cubebox.tools.builtin.datetime_tool import get_datetime as _get_datetime_impl


class DateTimeInput(BaseModel):
    include_time: bool = Field(
        default=False,
        description=(
            "Whether to include the current time (HH:MM:SS). "
            "Default is False, which returns only the date and day of week."
        ),
    )


async def _execute(
    tool_call_id: str,
    args: DateTimeInput,
    *,
    signal: object = None,
    on_update: object = None,
) -> AgentToolResult:
    """Cubepi-shaped execute wrapper around the pure datetime function."""
    del tool_call_id, signal, on_update
    result = _get_datetime_impl(include_time=args.include_time)
    return AgentToolResult(content=[TextContent(text=result)])


datetime_tool: AgentTool[DateTimeInput] = AgentTool(
    name="datetime",
    description=(
        "Get the current date and day of week. "
        "Optionally include the current time. "
        "Use this tool whenever you need to know the current date or time — "
        "do not guess or rely on training data."
    ),
    parameters=DateTimeInput,
    execute=_execute,
)
