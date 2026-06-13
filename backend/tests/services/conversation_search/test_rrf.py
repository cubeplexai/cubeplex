from cubebox.services.conversation_search.rrf import rrf_fuse


def test_same_doc_top_of_both_lists_wins() -> None:
    out = rrf_fuse(lexical=["a", "b", "c"], vector=["a", "x", "y"], k=60)
    assert out[0][0] == "a"


def test_lexical_only_doc_appears() -> None:
    out = rrf_fuse(lexical=["a"], vector=["b"], k=60)
    ids = {doc for doc, _ in out}
    assert ids == {"a", "b"}


def test_empty_legs_return_empty() -> None:
    assert rrf_fuse(lexical=[], vector=[], k=60) == []


def test_scores_descending() -> None:
    out = rrf_fuse(lexical=["a", "b"], vector=["b", "c"], k=60)
    scores = [s for _, s in out]
    assert scores == sorted(scores, reverse=True)
