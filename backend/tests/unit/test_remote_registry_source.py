import httpx
import pytest

from cubebox.skills.sources.base import TrustTier, decode_candidate_id
from cubebox.skills.sources.remote import RemoteRegistrySource


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
    src = RemoteRegistrySource(
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
    src = RemoteRegistrySource(
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
