"""Memory Management

Provides unified interface for short-term and long-term memory.
"""

from typing import Any


class MemoryManager:
    """Unified memory management interface"""

    def __init__(self) -> None:
        """Initialize memory manager"""
        self.short_term_memory: dict[str, list[Any]] = {}
        self.long_term_memory: dict[str, list[dict[str, Any]]] = {}
