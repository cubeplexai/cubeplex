"""Unit tests for the skills capability (find / preview / install)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import cubebox.agents.actions.capabilities.skills as _skills_mod
from cubebox.agents.actions.capabilities.skills import (
    FindInput,
    SkillDeps,
    _handle_find_impl,
)
from cubebox.agents.actions.context import ScopeContext
from cubebox.models.membership import Role


def _make_deps(
    *,
    registry: Any | None = None,
    catalog: Any | None = None,
    catalog_session: Any | None = None,
) -> SkillDeps:
    return SkillDeps(
        catalog=catalog or MagicMock(),
        catalog_session=catalog_session or MagicMock(),
        registry=registry or MagicMock(),
        org_id="org-test",
        org_slug="org-slug",
        workspace_id="ws-test",
    )


def _ctx() -> ScopeContext:
    return ScopeContext(
        org_id="org-test",
        workspace_id="ws-test",
        user_id="usr-test",
        role=Role.MEMBER,
    )


@dataclass
class _FakeCandidate:
    candidate_id: str
    name: str
    canonical_name: str
    description: str
    source_kind: str
    source_name: str
    repo: str | None
    trust: Any
    install_state: str
    install_count: int


class _TrustEnum:
    """Matches the .value attribute the handler reads off Trust."""

    def __init__(self, value: str) -> None:
        self.value = value


@pytest.mark.asyncio
async def test_find_returns_candidates_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeCandidate(
        candidate_id="cid-1",
        name="My Skill",
        canonical_name="myorg:my-skill",
        description="Does something useful",
        source_kind="local",
        source_name="org-catalog",
        repo="https://github.com/x/y",
        trust=_TrustEnum("official"),
        install_state="in_catalog",
        install_count=3,
    )
    fake_discovery = MagicMock()
    fake_discovery.discover = AsyncMock(return_value=[fake])
    monkeypatch.setattr(
        _skills_mod, "_SkillDiscoveryService", MagicMock(return_value=fake_discovery)
    )

    deps = _make_deps()
    result = await _handle_find_impl(deps, _ctx(), MagicMock(), FindInput(query="useful"))

    assert isinstance(result, dict)
    assert "candidates" in result and "hint" in result
    assert len(result["candidates"]) == 1
    c = result["candidates"][0]
    assert c["candidate_id"] == "cid-1"
    assert c["name"] == "My Skill"
    assert c["canonical_name"] == "myorg:my-skill"
    assert c["source"] == "local"
    assert c["trust"] == "official"
    assert c["unvetted"] is False  # local source → never unvetted

    # The handler must instantiate SkillDiscoveryService with deps.registry.
    _skills_mod._SkillDiscoveryService.assert_called_once_with(deps.registry)
    fake_discovery.discover.assert_awaited_once_with("useful", limit=5)
