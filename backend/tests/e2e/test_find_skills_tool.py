"""E2E: find_skills builtin tool (cubepi AgentTool).

Exercises create_find_skills_tool + SkillDiscoveryService end-to-end against the
real test DB (preinstalled skills seeded by app lifespan; exposed here via the
seeded_session_org_ws fixture).
"""

import pytest

from cubebox.skills.cache import SkillCache
from cubebox.skills.discovery import SkillDiscoveryService
from cubebox.skills.service import SkillCatalogService
from cubebox.skills.sources.registry import SkillsAdapterManager
from cubebox.tools.builtin.find_skills import FindSkillsInput, create_find_skills_tool


@pytest.mark.asyncio
async def test_find_skills_tool_returns_local_candidate(
    seeded_session_org_ws: tuple[object, str, str, str],
) -> None:
    session, org_id, org_slug, ws_id = seeded_session_org_ws
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncSession

    assert isinstance(session, AsyncSession)
    catalog = SkillCatalogService(
        session=session, cache=SkillCache(cache_root=Path("skills_cache"))
    )
    registry = await SkillsAdapterManager.build(
        session=session,
        catalog=catalog,
        org_id=org_id,
        org_slug=org_slug,
        workspace_id=ws_id,
    )
    tool = create_find_skills_tool(discovery=SkillDiscoveryService(registry))
    result = await tool.execute("tc-1", FindSkillsInput(query="research"))
    assert not result.is_error
    text = result.content[0].text
    assert "deep-research" in text
    assert "candidate_id" in text
