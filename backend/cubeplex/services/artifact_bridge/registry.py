"""Registry of callable artifact functions.

Registration is import-time: ``functions/__init__.py`` imports each handler
module, which calls :func:`register_function` at module scope. Lookup triggers
that import lazily so callers may import this module directly.
"""

from __future__ import annotations

import threading
from typing import Any

from cubeplex.services.artifact_bridge.types import (
    ArtifactFunction,
    ArtifactFunctionNotFound,
)

_FUNCTIONS: dict[str, ArtifactFunction] = {}
_load_lock = threading.Lock()
_loaded = False


def register_function(function: ArtifactFunction) -> None:
    """Add one callable capability. Duplicate names are a programming error."""
    if function.name in _FUNCTIONS:
        raise ValueError(f"artifact function {function.name!r} is already registered")
    _FUNCTIONS[function.name] = function


def _ensure_loaded() -> None:
    global _loaded
    if _loaded:
        return
    with _load_lock:
        if _loaded:
            return
        from cubeplex.services.artifact_bridge import functions  # noqa: F401

        _loaded = True


def get_function(name: str) -> ArtifactFunction:
    """Resolve a registered function by name."""
    _ensure_loaded()
    try:
        return _FUNCTIONS[name]
    except KeyError:
        raise ArtifactFunctionNotFound(name) from None


def catalogue() -> list[dict[str, Any]]:
    """Describe every registered function so clients can discover them."""
    _ensure_loaded()
    return [
        {
            "name": function.name,
            "description": function.description,
            "input_schema": function.input_model.model_json_schema(),
        }
        for function in _FUNCTIONS.values()
    ]
