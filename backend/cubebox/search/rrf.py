"""Reciprocal Rank Fusion. Sum of 1/(k+rank) across input ranked lists."""

from collections.abc import Iterable


def rrf_fuse(
    lexical: Iterable[str],
    vector: Iterable[str],
    k: int = 60,
) -> list[tuple[str, float]]:
    """Return ``[(id, fused_score), ...]`` ordered by descending score."""
    scores: dict[str, float] = {}
    for rank, doc_id in enumerate(lexical, start=1):
        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    for rank, doc_id in enumerate(vector, start=1):
        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
