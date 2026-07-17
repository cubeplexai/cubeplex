"""Text chunking for citation references.

Splits text into 200-300 character chunks using a three-level fallback:
paragraph boundaries → sentence boundaries → fixed character limit.
"""

import re

_SENTENCE_RE = re.compile(r"(?<=[。！？.!?\n])")


def _split_sentences(text: str) -> list[str]:
    """Split text by sentence boundaries, keeping delimiters attached."""
    parts = _SENTENCE_RE.split(text)
    return [p for p in parts if p.strip()]


def _hard_split(text: str, max_size: int) -> list[str]:
    """Split text into chunks of at most max_size characters."""
    return [text[i : i + max_size] for i in range(0, len(text), max_size)]


def chunk_text(
    text: str,
    *,
    min_size: int = 200,
    max_size: int = 300,
) -> list[str]:
    """Split text into chunks targeting min_size..max_size characters.

    Strategy:
    1. Split by paragraph (\\n\\n)
    2. Oversized paragraphs split by sentence boundaries
    3. Oversized sentences split by fixed character limit
    4. Undersized chunks merged with the next chunk

    Args:
        text: Input text to chunk.
        min_size: Minimum desired chunk size in characters.
        max_size: Maximum chunk size in characters.

    Returns:
        List of text chunks. Empty list if text is empty/whitespace.
    """
    text = text.strip()
    if not text:
        return []

    paragraphs = [p.strip() for p in re.split(r"\n\n+", text) if p.strip()]

    raw_chunks: list[str] = []
    for para in paragraphs:
        if len(para) <= max_size:
            raw_chunks.append(para)
            continue

        sentences = _split_sentences(para)
        if len(sentences) <= 1:
            raw_chunks.extend(_hard_split(para, max_size))
            continue

        current = ""
        for sentence in sentences:
            if len(sentence) > max_size:
                if current:
                    raw_chunks.append(current)
                    current = ""
                raw_chunks.extend(_hard_split(sentence, max_size))
            elif len(current) + len(sentence) > max_size:
                if current:
                    raw_chunks.append(current)
                current = sentence
            else:
                current += sentence
        if current:
            raw_chunks.append(current)

    merged: list[str] = []
    for chunk in raw_chunks:
        if merged and len(merged[-1]) < min_size and len(merged[-1]) + 1 + len(chunk) <= max_size:
            merged[-1] = merged[-1] + "\n" + chunk
        else:
            merged.append(chunk)

    if len(merged) > 1 and len(merged[-1]) < min_size:
        candidate = merged[-2] + "\n" + merged[-1]
        if len(candidate) <= max_size:
            merged[-2] = candidate
            merged.pop()

    return merged
