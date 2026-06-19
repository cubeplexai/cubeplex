"""Markdown normalization for Teams.

Teams supports most standard Markdown (bold, italic, code, links, lists,
tables) but NOT strikethrough (~~text~~). Also strips <at>...</at>
mention tags injected by Teams into inbound message text.
"""

from __future__ import annotations

import re

_FENCED_CODE_RE = re.compile(r"```[\s\S]*?```")
_INLINE_CODE_RE = re.compile(r"`[^`]+`")
_STRIKE_RE = re.compile(r"~~(.+?)~~")
_AT_TAG_RE = re.compile(r"<at[^>]*>[^<]*</at>\s*")


def normalize_for_teams(text: str) -> str:
    """Strip unsupported syntax from Markdown for Teams rendering."""
    placeholders: list[str] = []

    def _protect(m: re.Match[str]) -> str:
        placeholders.append(m.group(0))
        return f"\x00PH{len(placeholders) - 1}\x00"

    protected = _FENCED_CODE_RE.sub(_protect, text)
    protected = _INLINE_CODE_RE.sub(_protect, protected)

    protected = _STRIKE_RE.sub(r"\1", protected)

    result = protected
    for i, original in enumerate(placeholders):
        result = result.replace(f"\x00PH{i}\x00", original)
    return result


def strip_mention_tags(text: str) -> str:
    """Remove Teams <at>BotName</at> tags from inbound message text."""
    return _AT_TAG_RE.sub("", text).strip()
