"""show_widget builtin tool.

Declares the schema so the model can stream an HTML widget. execute() does no
real work - rendering happens entirely in the frontend, which reads the
streamed widget_code from tool_call_delta events. The ack just closes the
tool call in message history.
"""

from cubepi.agent.types import AgentTool, AgentToolResult
from cubepi.providers.base import TextContent
from pydantic import BaseModel, Field

from cubeplex.prompts.widget import WIDGET_TOOL_DESCRIPTION


class _ShowWidgetArgs(BaseModel):
    title: str = Field(description="Short snake_case identifier for the widget.")
    widget_code: str = Field(description="HTML fragment to render (no <html>/<body>).")
    width: int | None = Field(default=None, description="Optional preferred width in px.")
    height: int | None = Field(default=None, description="Optional preferred height in px.")


def make_show_widget_tool() -> AgentTool[_ShowWidgetArgs]:
    async def _show_widget(
        tool_call_id: str,
        args: _ShowWidgetArgs,
        *,
        signal: object = None,
        on_update: object = None,
    ) -> AgentToolResult:
        del tool_call_id, args, signal, on_update
        return AgentToolResult(content=[TextContent(text="Widget rendered.")])

    return AgentTool(
        name="show_widget",
        description=WIDGET_TOOL_DESCRIPTION,
        parameters=_ShowWidgetArgs,
        execute=_show_widget,
    )
