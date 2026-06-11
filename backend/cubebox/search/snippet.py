"""Build a short snippet around the first literal match. NFC + case-fold.

NFC + casefold can change the character count (e.g. German 'ß' -> 'ss',
Turkish 'İ' -> 'i̇'), so we cannot reuse indices from the normalized
string to slice the original. We build a normalized haystack alongside a
parallel mapping that, for each normalized-string index, records the
matching position in the original string. The mapping is then used to
translate the match start/end back to original-string offsets.
"""

import unicodedata
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Snippet:
    text: str
    match_offsets: list[tuple[int, int]] = field(default_factory=list)


def _normalise(s: str) -> str:
    return unicodedata.normalize("NFC", s).casefold()


def _build_haystack_norm(text: str) -> tuple[str, list[int]]:
    """Return (normalized_haystack, norm_to_orig_start).

    norm_to_orig_start[i] is the index in `text` of the original character
    that produced the normalized character at index i. An extra trailing
    entry (= len(text)) lets callers translate a half-open end index too.
    """
    parts: list[str] = []
    mapping: list[int] = []
    for i, ch in enumerate(text):
        normed = unicodedata.normalize("NFC", ch).casefold()
        parts.append(normed)
        mapping.extend([i] * len(normed))
    mapping.append(len(text))
    return "".join(parts), mapping


def extract_snippet(text: str, q: str, window: int = 160) -> Snippet:
    if not text:
        return Snippet(text="")
    needle = _normalise(q)
    haystack_norm, norm_to_orig = _build_haystack_norm(text)
    pos_norm = haystack_norm.find(needle) if needle else -1
    if pos_norm == -1:
        head = text[:window].rstrip()
        return Snippet(text=head + ("…" if len(text) > window else ""))
    # Translate normalized match span back to original-string offsets.
    orig_start = norm_to_orig[pos_norm]
    orig_end = norm_to_orig[pos_norm + len(needle)]
    half = window // 2
    start = max(0, orig_start - half)
    end = min(len(text), start + window)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    snippet_text = prefix + text[start:end] + suffix
    match_in_snippet_start = len(prefix) + (orig_start - start)
    match_in_snippet_end = len(prefix) + (orig_end - start)
    return Snippet(
        text=snippet_text,
        match_offsets=[(match_in_snippet_start, match_in_snippet_end)],
    )
