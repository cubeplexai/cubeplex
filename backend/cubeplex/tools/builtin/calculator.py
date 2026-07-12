"""Calculator tool, declared via ``@cubepi.tool``."""

from __future__ import annotations

import math
from typing import Any

from cubepi import tool
from pydantic import Field


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


@tool(
    name="calculator",
    description=(
        "Execute simple mathematical calculations. "
        "Supports basic arithmetic, trigonometric, and math functions."
    ),
)
async def calculator_tool(
    expression: str = Field(description="Mathematical expression to evaluate, e.g., '2 + 3 * 4'"),
) -> str:
    return _calculator_impl(expression)
