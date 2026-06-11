"""Build a short snippet around the first literal match. NFC + case-fold."""

import unicodedata
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Snippet:
    text: str
    match_offsets: list[tuple[int, int]] = field(default_factory=list)


def extract_snippet(text: str, q: str, window: int = 160) -> Snippet:
    if not text:
        return Snippet(text="")
    needle = _normalise(q)
    haystack_norm = _normalise(text)
    pos = haystack_norm.find(needle) if needle else -1
    if pos == -1:
        head = text[:window].rstrip()
        return Snippet(text=head + ("…" if len(text) > window else ""))
    half = window // 2
    start = max(0, pos - half)
    end = min(len(text), start + window)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    snippet_text = prefix + text[start:end] + suffix
    match_in_snippet_start = len(prefix) + (pos - start)
    match_in_snippet_end = match_in_snippet_start + len(needle)
    return Snippet(
        text=snippet_text,
        match_offsets=[(match_in_snippet_start, match_in_snippet_end)],
    )


def _normalise(s: str) -> str:
    return unicodedata.normalize("NFC", s).casefold()
