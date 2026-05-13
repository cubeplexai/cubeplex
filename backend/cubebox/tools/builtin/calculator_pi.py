"""Calculator tool ported to cubepi.AgentTool (M2.1)."""

from __future__ import annotations

from cubepi.agent.types import AgentTool, AgentToolResult
from cubepi.providers.base import TextContent
from pydantic import BaseModel, Field

from cubebox.tools.builtin.calculator import calculator as _calculator_impl


class CalculatorInput(BaseModel):
    """Input schema for the cubepi calculator tool."""

    expression: str = Field(description="Mathematical expression to evaluate, e.g., '2 + 3 * 4'")


async def _execute(
    tool_call_id: str,
    args: CalculatorInput,
    *,
    signal: object = None,
    on_update: object = None,
) -> AgentToolResult:
    """Cubepi-shaped execute wrapper around the pure calculator function."""
    del tool_call_id, signal, on_update
    result = _calculator_impl(args.expression)
    return AgentToolResult(content=[TextContent(text=result)])


calculator_tool: AgentTool[CalculatorInput] = AgentTool(
    name="calculator",
    description=(
        "Execute mathematical calculations safely. "
        "Supports basic arithmetic, trigonometric, and math functions."
    ),
    parameters=CalculatorInput,
    execute=_execute,
)
