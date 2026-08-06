"""Sliding-window chunker with soft token budgets (CJK-aware estimate).

Windows are sized with :func:`cubeplex.utils.tokens.char_token_weight`, not a
real BPE. Embedding backends do not share one tokenizer with us, so a soft
cap is enough: keep chunks in a stable size band without shipping or
downloading a vocab file.
"""

from __future__ import annotations

from dataclasses import dataclass

from cubeplex.utils.tokens import char_token_weight


@dataclass(frozen=True)
class MessageInput:
    seq: int
    text: str


@dataclass(frozen=True)
class Chunk:
    chunk_seq: int
    seq_lo: int
    seq_hi: int
    text: str


def chunk_messages(
    messages: list[MessageInput],
    target_tokens: int,
    overlap_tokens: int,
) -> list[Chunk]:
    """Build sliding-window chunks. seq_lo / seq_hi track which message seqs
    contributed to a chunk. Empty input → empty list.

    ``target_tokens`` / ``overlap_tokens`` are soft units from the shared
    estimator (not model-native tokens).
    """
    if not messages or target_tokens <= 0:
        return []

    # Flatten messages into a char stream with per-char seq + weight.
    # A single space between messages keeps text readable (same idea as the
    # old tiktoken path that inserted encode(" ") between messages).
    chars: list[str] = []
    seqs: list[int] = []
    weights: list[float] = []
    for m in messages:
        if not m.text:
            continue
        if chars:
            chars.append(" ")
            seqs.append(m.seq)
            weights.append(char_token_weight(" "))
        for ch in m.text:
            chars.append(ch)
            seqs.append(m.seq)
            weights.append(char_token_weight(ch))
    if not chars:
        return []

    n = len(chars)
    step = max(1e-6, float(target_tokens - max(0, overlap_tokens)))
    target = float(target_tokens)
    out: list[Chunk] = []
    start = 0
    while start < n:
        # Grow end until soft weight ≈ target_tokens (or EOF).
        acc = 0.0
        end = start
        while end < n and acc < target:
            acc += weights[end]
            end += 1
        text = "".join(chars[start:end])
        window_seqs = seqs[start:end]
        out.append(
            Chunk(
                chunk_seq=len(out),
                seq_lo=min(window_seqs),
                seq_hi=max(window_seqs),
                text=text,
            )
        )
        if end >= n:
            break
        # Advance by ~step weight so consecutive windows share ~overlap.
        advanced = 0.0
        nxt = start
        while nxt < end and advanced < step:
            advanced += weights[nxt]
            nxt += 1
        start = nxt if nxt > start else start + 1
    return out
