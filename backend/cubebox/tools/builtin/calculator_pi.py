"""Calculator tool ported to cubepi.AgentTool (M2.1)."""

from __future__ import annotations

import math
from typing import Any

from cubepi.agent.types import AgentTool, AgentToolResult
from cubepi.providers.base import TextContent
from pydantic import BaseModel, Field


class CalculatorInput(BaseModel):
    expression: str = Field(description="Mathematical expression to evaluate, e.g., '2 + 3 * 4'")


def _calculator_impl(expression: str) -> str:
    """Safely evaluate a mathematical expression using a restricted namespace."""
    try:
        safe_dict: dict[str, Any] = {
            "abs": abs,
            "round": round,
            "min": min,
            "max": max,
            "sum": sum,
            "pow": pow,
            "__builtins__": {},
        }
        safe_dict.update(
            {name: getattr(math, name) for name in dir(math) if not name.startswith("_")}
        )
        result = eval(expression, safe_dict)  # noqa: S307
        return f"Result: {result}"
    except ZeroDivisionError:
        return "Error: Division by zero"
    except ValueError as e:
        return f"Error: Invalid value - {e!s}"
    except SyntaxError:
        return "Error: Invalid expression syntax"
    except NameError as e:
        return f"Error: Undefined variable - {e!s}"
    except TypeError as e:
        return f"Error: Invalid operation - {e!s}"
    except Exception as e:  # noqa: BLE001
        return f"Error: {e!s}"


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
        "Execute simple mathematical calculations. "
        "Supports basic arithmetic, trigonometric, and math functions."
    ),
    parameters=CalculatorInput,
    execute=_execute,
)
