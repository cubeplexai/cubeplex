"""Unit tests for SkillsShAdapter using httpx.MockTransport."""

from __future__ import annotations

import httpx
import pytest

from cubebox.skills.sources.base import TrustTier, decode_candidate_id
from cubebox.skills.sources.skills_sh import SkillsShAdapter


def _make_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)

        # skills.sh search
        if "skills.sh/api/search" in url:
            return httpx.Response(
                200,
                json={
                    "skills": [
                        {
                            "name": "frontend-design",
                            "id": "frontend-design",
                            "source": "vercel-labs/skills",
                            "installs": 850,
                        }
                    ]
                },
            )

        # GitHub repo metadata (default_branch)
        if "api.github.com/repos/vercel-labs/skills" in url and "git/trees" not in url:
            return httpx.Response(200, json={"default_branch": "main"})

        # GitHub tree
        if "api.github.com/repos/vercel-labs/skills/git/trees/main" in url:
            return httpx.Response(
                200,
                json={
                    "tree": [
                        {"path": "frontend-design/SKILL.md", "type": "blob"},
                        {"path": "frontend-design/references/guide.md", "type": "blob"},
                        {"path": "other-skill/SKILL.md", "type": "blob"},
                    ]
                },
            )

        # GitHub raw files
        if "raw.githubusercontent.com" in url:
            if url.endswith("SKILL.md"):
                return httpx.Response(
                    200,
                    text=(
                        "---\nname: frontend-design\n"
                        "description: Build UIs\nversion: 1.2.0\n---\n# Frontend\n"
                    ),
                )
            if url.endswith("references/guide.md"):
                return httpx.Response(200, text="# Guide\n")

        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.fixture
def adapter() -> SkillsShAdapter:
    return SkillsShAdapter(
        source_id="sksrc-test-1",
        trust_tier=TrustTier.community,
        source_name="skills.sh",
        github_token=None,
        transport=_make_transport(),
    )


@pytest.mark.asyncio
async def test_search_returns_candidates(adapter: SkillsShAdapter) -> None:
    results = await adapter.search("frontend", limit=5)
    assert len(results) == 1
    c = results[0]
    assert c.name == "frontend-design"
    assert c.trust == TrustTier.community
    assert c.source_name == "skills.sh"
    assert c.install_count == 850


@pytest.mark.asyncio
async def test_search_encodes_branch_in_source_ref(adapter: SkillsShAdapter) -> None:
    results = await adapter.search("frontend", limit=5)
    kind, source_id, source_ref = decode_candidate_id(results[0].candidate_id)
    assert kind == "remote"
    assert source_id == "sksrc-test-1"
    # source_ref encodes branch resolved at search time
    assert source_ref == "vercel-labs/skills/main/frontend-design"


@pytest.mark.asyncio
async def test_search_returns_empty_on_api_error() -> None:
    def fail_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    bad_adapter = SkillsShAdapter(
        source_id="sksrc-x",
        trust_tier=TrustTier.untrusted,
        source_name="skills.sh",
        github_token=None,
        transport=httpx.MockTransport(fail_handler),
    )
    results = await bad_adapter.search("anything", limit=5)
    assert results == []


@pytest.mark.asyncio
async def test_fetch_downloads_skill_files(adapter: SkillsShAdapter) -> None:
    files = await adapter.fetch("vercel-labs/skills/main/frontend-design")
    assert "SKILL.md" in files
    assert b"Frontend" in files["SKILL.md"]
    assert "references/guide.md" in files
    # files from other skills must not appear
    assert not any("other-skill" in k for k in files)


@pytest.mark.asyncio
async def test_fetch_raises_on_missing_skill_md() -> None:
    def no_skill_md(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "git/trees" in url:
            return httpx.Response(200, json={"tree": []})
        return httpx.Response(200, json={"default_branch": "main"})

    bad = SkillsShAdapter(
        source_id="x",
        trust_tier=TrustTier.untrusted,
        source_name="s",
        github_token=None,
        transport=httpx.MockTransport(no_skill_md),
    )
    with pytest.raises(ValueError, match="SKILL.md"):
        await bad.fetch("owner/repo/main/slug")
