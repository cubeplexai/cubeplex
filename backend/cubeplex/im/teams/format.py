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


def _strip_at_tags(text: str) -> str:
    """Remove ``<at>...</at>`` mention tags and any trailing whitespace.

    Linear-time scan replacing a polynomial regex. The old
    ``<at[^>]*>[^<]*</at>\\s*`` backtracked super-linearly on adversarial input
    with many unclosed ``<at`` tags (``re.sub`` retried at every position).
    For each ``<at ...>`` opening tag this finds the first ``</at>`` after the
    ``>``, drops the span, and consumes the whitespace right after it (the
    original ``\\s*``). If no ``>`` or no ``</at>`` remains ahead, no later tag
    can close either, so the rest is emitted verbatim and the scan stops -
    that early stop is what keeps this O(n) instead of O(n^2).
    """
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        if text[i] == "<" and text.startswith("<at", i):
            gt = text.find(">", i + 3)
            if gt == -1:
                out.append(text[i:])
                break
            close = text.find("</at>", gt + 1)
            if close == -1:
                out.append(text[i:])
                break
            i = close + len("</at>")
            while i < n and text[i] in " \t\r\n":
                i += 1
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


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
    return _strip_at_tags(text).strip()
