"""Sliding-window chunker. Token counting via tiktoken cl100k_base."""

from dataclasses import dataclass

import tiktoken

_ENC = tiktoken.get_encoding("cl100k_base")


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
    """
    if not messages or target_tokens <= 0:
        return []
    tokens: list[int] = []
    token_seq: list[int] = []
    space = _ENC.encode(" ")
    for m in messages:
        if not m.text:
            continue
        encoded = _ENC.encode(m.text)
        if not encoded:
            continue
        tokens.extend(encoded)
        token_seq.extend([m.seq] * len(encoded))
        # Single-space boundary between messages keeps decoded text readable.
        tokens.extend(space)
        token_seq.extend([m.seq] * len(space))
    if not tokens:
        return []
    step = max(1, target_tokens - max(0, overlap_tokens))
    out: list[Chunk] = []
    i = 0
    while i < len(tokens):
        j = min(i + target_tokens, len(tokens))
        text = _ENC.decode(tokens[i:j])
        seqs = token_seq[i:j]
        out.append(
            Chunk(
                chunk_seq=len(out),
                seq_lo=min(seqs),
                seq_hi=max(seqs),
                text=text,
            )
        )
        if j == len(tokens):
            break
        i += step
    return out
