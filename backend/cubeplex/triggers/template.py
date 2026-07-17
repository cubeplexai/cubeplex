"""Prompt-template render with payload-field whitelist + <external_input>."""

from __future__ import annotations

import re
from typing import Any

from cubeplex.triggers.filter import _MISSING, _resolve

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([^{}]*?)\s*\}\}")


def _escape_close(value: str) -> str:
    """Escape </external_input> to <\\/external_input> in value."""
    return value.replace("</external_input>", "<\\/external_input>")


def render(
    template: str,
    payload: dict[str, Any],
    *,
    payload_fields: list[str],
    source_label: str,
) -> str:
    """Render template with whitelisted payload fields wrapped in tags.

    Placeholders are {{ <jsonpath> }}. Only paths in payload_fields are
    interpolated; others left as literal tokens. Interpolated values are
    wrapped in <external_input> tags with escaping for nested tags.

    Args:
        template: String with {{ jsonpath }} placeholders.
        payload: Dict to resolve paths from.
        payload_fields: Whitelist of jsonpath strings to interpolate.
        source_label: Label for the <external_input> source attribute.

    Returns:
        Rendered string with whitelisted placeholders wrapped.
    """
    whitelist = set(payload_fields)

    def replace(match: re.Match[str]) -> str:
        token = match.group(0)  # Full {{ ... }} token
        path = match.group(1)  # Content between braces (trimmed)
        if path not in whitelist:
            return token  # Non-whitelisted → leave literal
        resolved = _resolve(payload, path)
        rendered = "" if resolved is _MISSING else str(resolved)
        safe = _escape_close(rendered)
        attrs = f'source="{source_label}" path="{path}"'
        return f"<external_input {attrs}>{safe}</external_input>"

    return _PLACEHOLDER_RE.sub(replace, template)
