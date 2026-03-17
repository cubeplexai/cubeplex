"""Checkpointer module for LangGraph conversation persistence."""

from typing import Any

_checkpointer: Any = None


def get_checkpointer() -> Any:
    """Get the global LangGraph checkpointer instance."""
    return _checkpointer


def set_checkpointer(checkpointer: Any) -> None:
    """Set the global LangGraph checkpointer instance."""
    global _checkpointer
    _checkpointer = checkpointer
