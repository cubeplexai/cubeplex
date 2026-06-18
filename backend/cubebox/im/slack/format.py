"""Markdown → Slack mrkdwn conversion.

Slack's mrkdwn dialect differs from standard Markdown:
  **bold**   → *bold*
  *italic*   → _italic_
  [t](url)   → <url|t>
  ~~strike~~ → ~strike~

Code blocks (``` and `) and Slack special tokens (<@U...>, <#C...>,
<!here>, etc.) are preserved verbatim — no conversion is applied
inside them.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Placeholder-based protection: extract code blocks / inline code / Slack
# tokens before converting, then reinsert after.
# ---------------------------------------------------------------------------

# Order matters: fenced code blocks before inline code.
_FENCED_CODE_RE = re.compile(r"```[\s\S]*?```")
_INLINE_CODE_RE = re.compile(r"`[^`]+`")
# Slack special tokens: user mentions, channel refs, subteam, special
# commands (<!here>, <!channel>, <!everyone>), and emoji shortcodes.
_SLACK_TOKEN_RE = re.compile(r"<[@#!][^>]+>")

# ---------------------------------------------------------------------------
# Conversion regexes (applied only to unprotected text).
# ---------------------------------------------------------------------------

# Bold: **text** → *text*  (must come before italic)
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
# Italic: *text* → _text_  (single asterisk, not inside bold)
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*]+?)\*(?!\*)")
# Links: [text](url) → <url|text>
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
# Strikethrough: ~~text~~ → ~text~
_STRIKE_RE = re.compile(r"~~(.+?)~~")


def markdown_to_slack_mrkdwn(text: str) -> str:
    """Convert standard Markdown to Slack mrkdwn, preserving code and Slack tokens."""
    if not text:
        return text

    # 1. Extract protected spans and replace with placeholders.
    placeholders: list[str] = []

    def _protect(match: re.Match[str]) -> str:
        idx = len(placeholders)
        placeholders.append(match.group(0))
        return f"\x00PH{idx}\x00"

    result = _FENCED_CODE_RE.sub(_protect, text)
    result = _INLINE_CODE_RE.sub(_protect, result)
    result = _SLACK_TOKEN_RE.sub(_protect, result)

    # 2. Apply conversions on unprotected text.
    #    Bold must be converted before italic, but the result *text* would
    #    be re-matched by the italic regex. Protect bold output with
    #    placeholders, apply italic, then restore.
    bold_outputs: list[str] = []

    def _bold_replace(match: re.Match[str]) -> str:
        idx = len(bold_outputs)
        bold_outputs.append(f"*{match.group(1)}*")
        return f"\x00BD{idx}\x00"

    result = _BOLD_RE.sub(_bold_replace, result)
    result = _ITALIC_RE.sub(r"_\1_", result)
    for idx, bold_text in enumerate(bold_outputs):
        result = result.replace(f"\x00BD{idx}\x00", bold_text)
    result = _LINK_RE.sub(r"<\2|\1>", result)
    result = _STRIKE_RE.sub(r"~\1~", result)

    # 3. Reinsert protected spans.
    for idx, original in enumerate(placeholders):
        result = result.replace(f"\x00PH{idx}\x00", original)

    return result
