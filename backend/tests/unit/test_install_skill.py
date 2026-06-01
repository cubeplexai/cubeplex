"""Unit tests for the install_skill agent tool."""

from __future__ import annotations

import json

import pytest

from cubebox.skills.discovery import InstallResult, SkillInstallError
from cubebox.tools.builtin.install_skill import InstallSkillInput, create_install_skill_tool


class _FakeInstallService:
    def __init__(self, result: InstallResult | Exception) -> None:
        self._result = result

    async def install(self, candidate_id: str) -> InstallResult:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


@pytest.mark.asyncio
async def test_install_skill_success() -> None:
    from cubebox.skills.sources.base import encode_candidate_id

    candidate_id = encode_candidate_id("remote", "owner/repo/main/skill", source_id="src-1")
    svc = _FakeInstallService(
        InstallResult(
            canonical_name="myorg:my-skill",
            skill_id="skl-abc",
            installed_version="1.0.0",
        )
    )

    tool = create_install_skill_tool(install_service_factory=lambda: svc)
    result = await tool.execute("tc-1", InstallSkillInput(candidate_id=candidate_id))

    assert not result.is_error
    out = json.loads(result.content[0].text)
    assert out["installed"] is True
    assert out["canonical_name"] == "myorg:my-skill"
    assert out["version"] == "1.0.0"


@pytest.mark.asyncio
async def test_install_skill_error_propagates() -> None:
    from cubebox.skills.sources.base import encode_candidate_id

    candidate_id = encode_candidate_id("remote", "owner/repo/main/skill", source_id="src-1")
    svc = _FakeInstallService(SkillInstallError("trust tier too low"))

    tool = create_install_skill_tool(install_service_factory=lambda: svc)
    result = await tool.execute("tc-2", InstallSkillInput(candidate_id=candidate_id))

    assert result.is_error
    assert "trust tier too low" in result.content[0].text


@pytest.mark.asyncio
async def test_install_bad_candidate_id_returns_error() -> None:
    svc = _FakeInstallService(InstallResult("x", "y", "1.0"))
    tool = create_install_skill_tool(install_service_factory=lambda: svc)
    result = await tool.execute("tc-3", InstallSkillInput(candidate_id="!!!bad!!!"))

    assert result.is_error
