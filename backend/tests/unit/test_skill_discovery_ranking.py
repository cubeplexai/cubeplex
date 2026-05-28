from cubebox.skills.discovery import rank_candidates
from cubebox.skills.sources.base import SkillCandidate, TrustTier


def _c(
    name,
    *,
    desc="",
    trust=TrustTier.untrusted,
    stars=None,
    kind="remote",
    keywords=None,
):
    return SkillCandidate(
        candidate_id=f"{kind}-{name}",
        name=name,
        canonical_name=name if kind == "local" else f"acme:{name}",
        description=desc,
        source_kind=kind,
        source_ref=name,
        keywords=keywords or [],
        trust=trust,
        stars=stars,
    )


def test_exact_name_match_ranks_first():
    cands = [_c("slide-deck", desc="slides"), _c("deck", desc="exact deck match")]
    ranked = rank_candidates(cands, query="deck", limit=5)
    assert ranked[0].name == "deck"


def test_trust_then_popularity_breaks_ties():
    a = _c("a", desc="data tool", trust=TrustTier.community, stars=10)
    b = _c("b", desc="data tool", trust=TrustTier.official, stars=1)
    c = _c("c", desc="data tool", trust=TrustTier.community, stars=99)
    ranked = rank_candidates([a, b, c], query="data", limit=5)
    assert ranked[0].name == "b"  # official beats community
    assert [x.name for x in ranked[1:]] == ["c", "a"]  # then stars desc


def test_dedupe_local_wins_against_remote_twin():
    local = _c("frontend-design", kind="local")  # canonical "frontend-design"
    remote = SkillCandidate(
        candidate_id="remote-fd",
        name="frontend-design",
        canonical_name="acme:frontend-design",
        description="",
        source_kind="remote",
        source_ref="x/y",
        keywords=[],
    )
    ranked = rank_candidates([remote, local], query="frontend", limit=5)
    assert len(ranked) == 1
    assert ranked[0].source_kind == "local"
    assert ranked[0].canonical_name == "frontend-design"


def test_limit_applied():
    cands = [_c(f"s{i}", desc="thing") for i in range(10)]
    assert len(rank_candidates(cands, query="thing", limit=3)) == 3


def test_plain_language_query_matches_tokens():
    target = _c("slide-deck", desc="Build presentations", keywords=["slides", "deck"])
    noise = _c("data-pipeline", desc="ETL jobs", keywords=["etl"])
    ranked = rank_candidates([noise, target], query="make a slide deck", limit=5)
    assert ranked[0].name == "slide-deck"


def test_single_keyword_token_matches():
    target = _c("deck-builder", desc="", keywords=["slides"])
    ranked = rank_candidates([_c("unrelated", desc="x"), target], query="slides", limit=5)
    assert ranked[0].name == "deck-builder"
