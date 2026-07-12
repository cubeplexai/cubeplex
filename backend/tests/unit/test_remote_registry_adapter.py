import httpx
import pytest

from cubeplex.skills.sources.base import TrustTier, decode_candidate_id
from cubeplex.skills.sources.remote import RemoteRegistryAdapter


def _registry_app() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search":
            return httpx.Response(
                200,
                json={
                    "skills": [
                        {
                            "name": "slide-deck",
                            "description": "Build slide decks",
                            "keywords": ["slides", "deck"],
                            "ref": "acme/skills/tree/main/skills/slide-deck",
                            "stars": 1200,
                            "installs": 50,
                        }
                    ]
                },
            )
        if request.url.path.startswith("/tree/"):
            return httpx.Response(
                200,
                json={"files": ["SKILL.md", "references/style.md", "scripts/run.py"]},
            )
        if request.url.path.endswith("/SKILL.md"):
            return httpx.Response(
                200,
                text=(
                    "---\nname: slide-deck\ndescription: Build slide decks\n"
                    "version: 1.0.0\n---\n# x\n"
                ),
            )
        if request.url.path.endswith("/references/style.md"):
            return httpx.Response(200, text="# style guide\n")
        if request.url.path.endswith("/scripts/run.py"):
            return httpx.Response(200, text="print('hi')\n")
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_search_normalizes_and_computes_canonical_name():
    src = RemoteRegistryAdapter(
        source_id="sksrc-1",
        base_url="https://reg.test",
        trust_tier=TrustTier.community,
        org_slug="acme",
        transport=_registry_app(),
    )
    cands = await src.search("slides", limit=5)
    assert len(cands) == 1
    c = cands[0]
    assert c.name == "slide-deck"
    assert c.canonical_name == "acme:slide-deck"
    assert c.source_ref == "acme/skills/tree/main/skills/slide-deck"
    assert c.trust == TrustTier.community
    assert c.stars == 1200
    assert decode_candidate_id(c.candidate_id) == (
        "remote",
        "sksrc-1",
        "acme/skills/tree/main/skills/slide-deck",
    )


@pytest.mark.asyncio
async def test_fetch_imports_whole_subpath_tree_not_just_skill_md():
    src = RemoteRegistryAdapter(
        source_id="sksrc-1",
        base_url="https://reg.test",
        trust_tier=TrustTier.community,
        org_slug="acme",
        transport=_registry_app(),
    )
    files = await src.fetch("acme/skills/tree/main/skills/slide-deck")
    assert set(files) == {"SKILL.md", "references/style.md", "scripts/run.py"}
    assert b"slide-deck" in files["SKILL.md"]
    assert b"style guide" in files["references/style.md"]


def _big_bundle_app(file_count: int, file_size: int) -> httpx.MockTransport:
    """Registry returning ``file_count`` files of ``file_size`` bytes each.

    Lets tests pile up cumulative bytes without any single file tripping
    the per-file cap — exercises the bundle-total guard specifically.
    """
    files_list = ["SKILL.md"] + [f"f{i}.bin" for i in range(file_count - 1)]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/tree/"):
            return httpx.Response(200, json={"files": files_list})
        if request.url.path.endswith("/SKILL.md"):
            return httpx.Response(200, content=b"x" * file_size)
        return httpx.Response(200, content=b"x" * file_size)

    return httpx.MockTransport(handler)


def _huge_tree_app(tree_size_bytes: int) -> httpx.MockTransport:
    """Registry returning a tree manifest payload of ``tree_size_bytes``."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/tree/"):
            # Pad a single bogus entry so the JSON body exceeds the cap.
            pad = "x" * tree_size_bytes
            return httpx.Response(
                200,
                json={"files": [pad]},
            )
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _huge_search_app(payload_size_bytes: int) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search":
            # Pad a single bogus skill entry so the body exceeds the cap.
            pad = "x" * payload_size_bytes
            return httpx.Response(
                200,
                json={"skills": [{"name": "x", "ref": "x", "description": pad}]},
            )
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_search_rejects_oversized_response_before_decoding():
    # 3 MB search payload, cap is 2 MB.
    src = RemoteRegistryAdapter(
        source_id="sksrc-1",
        base_url="https://reg.test",
        trust_tier=TrustTier.community,
        org_slug="acme",
        transport=_huge_search_app(payload_size_bytes=3 * 1024 * 1024),
    )
    with pytest.raises(ValueError, match="search response exceeds cap"):
        await src.search("anything", limit=5)


@pytest.mark.asyncio
async def test_fetch_rejects_oversized_tree_manifest_before_parsing():
    # 2 MB tree payload, cap is 1 MB.
    src = RemoteRegistryAdapter(
        source_id="sksrc-1",
        base_url="https://reg.test",
        trust_tier=TrustTier.community,
        org_slug="acme",
        transport=_huge_tree_app(tree_size_bytes=2 * 1024 * 1024),
    )
    with pytest.raises(ValueError, match="tree manifest exceeds cap"):
        await src.fetch("acme/skills/tree/main/skills/big")


@pytest.mark.asyncio
async def test_fetch_stops_at_bundle_cap_even_when_each_file_is_within_per_file_cap():
    # 8 files × 8 MB = 64 MB > 50 MB bundle cap, but each file is under
    # the 10 MB per-file cap so only the bundle guard can stop this.
    src = RemoteRegistryAdapter(
        source_id="sksrc-1",
        base_url="https://reg.test",
        trust_tier=TrustTier.community,
        org_slug="acme",
        transport=_big_bundle_app(file_count=8, file_size=8 * 1024 * 1024),
    )
    with pytest.raises(ValueError, match="bundle exceeds cap"):
        await src.fetch("acme/skills/tree/main/skills/big")
