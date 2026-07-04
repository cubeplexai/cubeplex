"""Shared reasoning defaults."""

from __future__ import annotations

from typing import Any

from cubepi.providers.base import ReasoningControl

DEFAULT_REASONING: dict[str, Any] = ReasoningControl().model_dump()
