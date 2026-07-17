"""Text command recognition for Teams.

Teams does not have a native slash-command registry like Slack/Discord.
Instead we recognize ``/link <email>`` (or ``link <email>``) as text
patterns in inbound messages and short-circuit before normal ingest.
"""

from __future__ import annotations

import re

_LINK_RE = re.compile(
    r"^\s*/?link\s+(\S+@\S+\.\S+)\s*$",
    re.IGNORECASE,
)


def parse_link_command(text: str) -> str | None:
    """Extract email from a /link command. Returns None if not a match."""
    m = _LINK_RE.match(text)
    return m.group(1).strip().lower() if m else None
