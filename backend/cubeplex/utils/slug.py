"""Slugify provider names into stable, URL-safe identifiers."""

from __future__ import annotations

import re

_NON_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """Lowercase, keep ascii alnum, collapse other runs to a single hyphen.

    Non-ascii characters are dropped (they map to nothing). An empty result
    (e.g. the name was all punctuation or non-ascii) falls back to ``provider``.
    """
    lowered = name.lower()
    collapsed = _NON_SLUG.sub("-", lowered).strip("-")
    return collapsed or "provider"
