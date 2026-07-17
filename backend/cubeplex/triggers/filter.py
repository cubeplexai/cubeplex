"""Declarative AND/OR + JSONPath filter matcher (pure)."""

from __future__ import annotations

from typing import Any


class _Missing:
    """Sentinel for missing keys in payload traversal."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "<MISSING>"


_MISSING: _Missing = _Missing()


def _resolve(payload: Any, path: str) -> Any:
    """Resolve a dot-separated path into payload.

    Returns _MISSING if any intermediate key is not found.
    """
    node: Any = payload
    for part in path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return _MISSING
    return node


def _eval_leaf(payload: dict[str, Any], leaf: dict[str, Any]) -> bool:
    """Evaluate a leaf filter node against payload."""
    path = leaf["path"]
    op = leaf["op"]
    value = leaf.get("value")
    resolved = _resolve(payload, path)

    if op == "eq":
        return resolved is not _MISSING and resolved == value
    if op == "neq":
        return resolved is _MISSING or resolved != value
    if op == "exists":
        return resolved is not _MISSING
    if op == "contains":
        if isinstance(resolved, str) and isinstance(value, str):
            return value in resolved
        if isinstance(resolved, list):
            return value in resolved
        return False
    if op == "in":
        if not isinstance(value, list):
            msg = f"'in' op requires list value, got {type(value).__name__}"
            raise ValueError(msg)
        return resolved is not _MISSING and resolved in value
    raise ValueError(f"unknown filter op: {op!r}")


def matches(filter_tree: dict[str, Any] | None, payload: dict[str, Any]) -> bool:
    """Check if a filter tree matches a payload.

    None filter matches everything.
    Nodes can be combinators (and/or) or leaf filters.
    """
    if filter_tree is None:
        return True
    if "and" in filter_tree:
        children = filter_tree["and"]
        if not children:
            raise ValueError("'and' node requires at least one child")
        return all(matches(c, payload) for c in children)
    if "or" in filter_tree:
        children = filter_tree["or"]
        if not children:
            raise ValueError("'or' node requires at least one child")
        return any(matches(c, payload) for c in children)
    return _eval_leaf(payload, filter_tree)
