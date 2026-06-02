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


# --- preview tests ---

from cubebox.agents.actions.capabilities.skills import (  # noqa: E402
    PreviewInput,
    _handle_preview_impl,
)
from cubebox.agents.actions.types import ActionInvalidInput  # noqa: E402


@pytest.mark.asyncio
async def test_preview_bad_candidate_id_raises_invalid_input() -> None:
    deps = _make_deps()
    with pytest.raises(ActionInvalidInput, match="BAD_CANDIDATE_ID"):
        await _handle_preview_impl(
            deps,
            _ctx(),
            MagicMock(),
            PreviewInput(candidate_id="!!!bad!!!"),
        )


@pytest.mark.asyncio
async def test_preview_local_returns_content(monkeypatch: pytest.MonkeyPatch) -> None:
    from cubebox.skills.sources.base import encode_candidate_id

    fake_skill = MagicMock(
        id="skl-1",
        source="preinstalled",
        owner_org_id="org-test",
        current_version="1.0.0",
    )
    fake_skill.name = "local-skill"
    fake_version = MagicMock(id="skv-1")

    skill_repo = MagicMock()
    skill_repo.get = AsyncMock(return_value=fake_skill)
    tomb_repo = MagicMock()
    tomb_repo.get = AsyncMock(return_value=None)
    version_repo = MagicMock()
    version_repo.find = AsyncMock(return_value=fake_version)

    monkeypatch.setattr(_skills_mod, "_SkillRepository", lambda _s: skill_repo)
    monkeypatch.setattr(
        _skills_mod,
        "_OrgPreinstalledTombstoneRepository",
        lambda _s: tomb_repo,
    )
    monkeypatch.setattr(_skills_mod, "_SkillVersionRepository", lambda _s: version_repo)

    fake_catalog = MagicMock()
    fake_catalog.fetch_skill_md = AsyncMock(
        return_value="---\nname: local-skill\n---\n# Local Skill"
    )

    deps = _make_deps(catalog=fake_catalog)
    cid = encode_candidate_id("local", "skl-1", source_id="local")

    result = await _handle_preview_impl(
        deps,
        _ctx(),
        MagicMock(),
        PreviewInput(candidate_id=cid),
    )
    assert isinstance(result, dict)
    assert result["candidate_id"] == cid
    assert result["name"] == "local-skill"
    assert "Local Skill" in result["content"]


@pytest.mark.asyncio
async def test_preview_remote_missing_source_raises() -> None:
    from cubebox.skills.sources.base import encode_candidate_id

    registry = MagicMock()
    registry.adapter_by_id = MagicMock(return_value=None)
    deps = _make_deps(registry=registry)
    cid = encode_candidate_id("remote", "owner/repo/main/skill", source_id="src-x")

    with pytest.raises(ActionInvalidInput, match="SOURCE_NOT_FOUND"):
        await _handle_preview_impl(
            deps,
            _ctx(),
            MagicMock(),
            PreviewInput(candidate_id=cid),
        )


# --- install tests ---

from cubebox.agents.actions.capabilities.skills import (  # noqa: E402
    InstallInput,
    _handle_install_impl,
)
from cubebox.skills.discovery import InstallResult, SkillInstallError  # noqa: E402


@pytest.mark.asyncio
async def test_install_bad_candidate_id_raises_invalid_input() -> None:
    deps = _make_deps()
    with pytest.raises(ActionInvalidInput, match="BAD_CANDIDATE_ID"):
        await _handle_install_impl(
            deps,
            _ctx(),
            MagicMock(),
            InstallInput(candidate_id="!!!bad!!!"),
        )


@pytest.mark.asyncio
async def test_install_success_returns_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    from cubebox.skills.sources.base import encode_candidate_id

    fake_svc = MagicMock()
    fake_svc.install = AsyncMock(
        return_value=InstallResult(
            canonical_name="myorg:my-skill",
            skill_id="skl-abc",
            installed_version="1.0.0",
        )
    )
    monkeypatch.setattr(_skills_mod, "_SkillInstallService", lambda **_kw: fake_svc)
    monkeypatch.setattr(_skills_mod, "_SkillPublishService", lambda **_kw: MagicMock())

    deps = _make_deps()
    cid = encode_candidate_id("remote", "owner/repo/main/skill", source_id="src-1")
    fake_session = MagicMock()

    result = await _handle_install_impl(
        deps,
        _ctx(),
        fake_session,
        InstallInput(candidate_id=cid),
    )
    assert result == {
        "installed": True,
        "canonical_name": "myorg:my-skill",
        "version": "1.0.0",
    }
    fake_svc.install.assert_awaited_once_with(cid)


@pytest.mark.asyncio
async def test_install_error_raises_invalid_input(monkeypatch: pytest.MonkeyPatch) -> None:
    from cubebox.skills.sources.base import encode_candidate_id

    fake_svc = MagicMock()
    fake_svc.install = AsyncMock(side_effect=SkillInstallError("trust tier too low"))
    monkeypatch.setattr(_skills_mod, "_SkillInstallService", lambda **_kw: fake_svc)
    monkeypatch.setattr(_skills_mod, "_SkillPublishService", lambda **_kw: MagicMock())

    deps = _make_deps()
    cid = encode_candidate_id("remote", "owner/repo/main/skill", source_id="src-1")

    with pytest.raises(ActionInvalidInput, match="trust tier too low"):
        await _handle_install_impl(
            deps,
            _ctx(),
            MagicMock(),
            InstallInput(candidate_id=cid),
        )


# --- mutation gate ---

from collections.abc import AsyncIterator  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402

from cubebox.agents.actions.builder import build_capability_tool  # noqa: E402
from cubebox.agents.actions.capabilities.skills import build_skills_capability  # noqa: E402


@asynccontextmanager
async def _fake_ctx_factory() -> AsyncIterator[tuple[ScopeContext, Any]]:
    yield (_ctx(), MagicMock())


def test_skills_capability_mutation_gate() -> None:
    deps = _make_deps()
    cap = build_skills_capability(deps)
    # Sanity: 3 operations declared
    assert {op.name for op in cap.operations} == {"find", "preview", "install"}
    assert next(op for op in cap.operations if op.name == "install").mutates is True
    assert next(op for op in cap.operations if op.name == "find").mutates is False
    assert next(op for op in cap.operations if op.name == "preview").mutates is False

    # With mutations allowed, the schema should mention all three ops.
    tool_full = build_capability_tool(cap, _fake_ctx_factory, allow_mutations=True)
    assert tool_full is not None
    schema_full = str(tool_full.parameters.model_json_schema())
    assert "Op_find" in schema_full
    assert "Op_preview" in schema_full
    assert "Op_install" in schema_full

    # Without mutations, install is dropped.
    tool_ro = build_capability_tool(cap, _fake_ctx_factory, allow_mutations=False)
    assert tool_ro is not None
    schema_ro = str(tool_ro.parameters.model_json_schema())
    assert "Op_install" not in schema_ro
    assert "Op_find" in schema_ro
    assert "Op_preview" in schema_ro
