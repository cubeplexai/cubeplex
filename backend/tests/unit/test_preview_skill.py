"""Unit tests for the preview_skill agent tool."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from cubebox.tools.builtin.preview_skill import PreviewSkillInput, create_preview_skill_tool


class _FakeSkillVersion:
    def __init__(self, sv_id: str) -> None:
        self.id = sv_id


class _FakeSkill:
    def __init__(
        self,
        skill_id: str,
        current_version: str,
        source: str = "preinstalled",
        name: str = "fake-skill",
    ) -> None:
        self.id = skill_id
        self.current_version = current_version
        self.source = source
        self.owner_org_id: str | None = None
        self.name = name


class _FakeCatalog:
    def __init__(self, content: str) -> None:
        self._content = content

    async def fetch_skill_md(self, skill_version_id: str) -> str:
        return self._content


class _FakeAdapter:
    def __init__(self, files: dict[str, bytes] | Exception) -> None:
        self._files = files

    async def fetch(self, source_ref: str) -> dict[str, bytes]:
        if isinstance(self._files, Exception):
            raise self._files
        return self._files


class _FakeRegistry:
    def __init__(self, adapter: _FakeAdapter | None) -> None:
        self._adapter = adapter

    def adapter_by_id(self, source_id: str) -> _FakeAdapter | None:
        return self._adapter


class _FakeSkillRepo:
    def __init__(self, skill: _FakeSkill | None) -> None:
        self._skill = skill

    async def get(self, skill_id: str) -> _FakeSkill | None:
        return self._skill


class _FakeSkillVersionRepo:
    def __init__(self, sv: _FakeSkillVersion | None) -> None:
        self._sv = sv

    async def find(self, skill_id: str, version: str) -> _FakeSkillVersion | None:
        return self._sv


class _FakeTombstoneRepo:
    def __init__(self, tombstone: object | None) -> None:
        self._tombstone = tombstone

    async def get(self, org_id: str, skill_id: str) -> object | None:
        return self._tombstone


class _FakeSession:
    pass


@pytest.mark.asyncio
async def test_preview_remote_returns_skill_md() -> None:
    from cubebox.skills.sources.base import encode_candidate_id

    candidate_id = encode_candidate_id(
        "remote", "owner/repo/main/skills/my-skill", source_id="src-1"
    )
    adapter = _FakeAdapter({"SKILL.md": b"# My Skill\nDoes stuff.", "extra.txt": b"ignore"})
    registry = _FakeRegistry(adapter)
    catalog = _FakeCatalog("irrelevant")

    tool = create_preview_skill_tool(
        session=_FakeSession(),
        registry=registry,
        catalog=catalog,
        org_id="org-1",
    )
    result = await tool.execute("tc-1", PreviewSkillInput(candidate_id=candidate_id))

    assert not result.is_error
    out = json.loads(result.content[0].text)
    assert out["content"] == "# My Skill\nDoes stuff."
    assert out["candidate_id"] == candidate_id


@pytest.mark.asyncio
async def test_preview_remote_no_adapter_returns_error() -> None:
    from cubebox.skills.sources.base import encode_candidate_id

    candidate_id = encode_candidate_id("remote", "owner/repo/main/skill", source_id="src-x")
    registry = _FakeRegistry(adapter=None)
    catalog = _FakeCatalog("irrelevant")

    tool = create_preview_skill_tool(
        session=_FakeSession(),
        registry=registry,
        catalog=catalog,
        org_id="org-1",
    )
    result = await tool.execute("tc-2", PreviewSkillInput(candidate_id=candidate_id))

    assert result.is_error
    assert "SOURCE_NOT_FOUND" in result.content[0].text


@pytest.mark.asyncio
async def test_preview_remote_missing_skill_md_returns_error() -> None:
    from cubebox.skills.sources.base import encode_candidate_id

    candidate_id = encode_candidate_id("remote", "owner/repo/main/skill", source_id="src-1")
    adapter = _FakeAdapter({"README.md": b"no SKILL.md here"})
    registry = _FakeRegistry(adapter)
    catalog = _FakeCatalog("irrelevant")

    tool = create_preview_skill_tool(
        session=_FakeSession(),
        registry=registry,
        catalog=catalog,
        org_id="org-1",
    )
    result = await tool.execute("tc-3", PreviewSkillInput(candidate_id=candidate_id))

    assert result.is_error
    assert "SKILL_MD_MISSING" in result.content[0].text


@pytest.mark.asyncio
async def test_preview_bad_candidate_id_returns_error() -> None:
    registry = _FakeRegistry(adapter=None)
    catalog = _FakeCatalog("irrelevant")

    tool = create_preview_skill_tool(
        session=_FakeSession(),
        registry=registry,
        catalog=catalog,
        org_id="org-1",
    )
    result = await tool.execute("tc-4", PreviewSkillInput(candidate_id="not-base64-valid!!!"))

    assert result.is_error
    assert "BAD_CANDIDATE_ID" in result.content[0].text


@pytest.mark.asyncio
async def test_preview_local_success() -> None:
    from cubebox.skills.sources.base import encode_candidate_id

    skill = _FakeSkill("skl-1", "1.0.0", name="my-skill")
    sv = _FakeSkillVersion("sv-1")
    catalog = _FakeCatalog("# Local Skill\nContent here.")

    candidate_id = encode_candidate_id("local", "skl-1")
    with (
        patch("cubebox.repositories.skill.SkillRepository", return_value=_FakeSkillRepo(skill)),
        patch(
            "cubebox.repositories.skill.SkillVersionRepository",
            return_value=_FakeSkillVersionRepo(sv),
        ),
        patch(
            "cubebox.repositories.skill.OrgPreinstalledTombstoneRepository",
            return_value=_FakeTombstoneRepo(None),
        ),
    ):
        tool = create_preview_skill_tool(
            session=_FakeSession(),
            registry=_FakeRegistry(adapter=None),
            catalog=catalog,
            org_id="org-1",
        )
        result = await tool.execute("tc-5", PreviewSkillInput(candidate_id=candidate_id))

    assert not result.is_error
    out = json.loads(result.content[0].text)
    assert out["content"] == "# Local Skill\nContent here."
    assert out["name"] == "my-skill"
    assert out["candidate_id"] == candidate_id


@pytest.mark.asyncio
async def test_preview_local_skill_not_found() -> None:
    from cubebox.skills.sources.base import encode_candidate_id

    candidate_id = encode_candidate_id("local", "skl-missing")
    with (
        patch("cubebox.repositories.skill.SkillRepository", return_value=_FakeSkillRepo(None)),
        patch(
            "cubebox.repositories.skill.SkillVersionRepository",
            return_value=_FakeSkillVersionRepo(None),
        ),
        patch(
            "cubebox.repositories.skill.OrgPreinstalledTombstoneRepository",
            return_value=_FakeTombstoneRepo(None),
        ),
    ):
        tool = create_preview_skill_tool(
            session=_FakeSession(),
            registry=_FakeRegistry(adapter=None),
            catalog=_FakeCatalog("irrelevant"),
            org_id="org-1",
        )
        result = await tool.execute("tc-6", PreviewSkillInput(candidate_id=candidate_id))

    assert result.is_error
    assert "SKILL_NOT_FOUND" in result.content[0].text


@pytest.mark.asyncio
async def test_preview_local_skill_version_not_found() -> None:
    from cubebox.skills.sources.base import encode_candidate_id

    skill = _FakeSkill("skl-1", "1.0.0", name="my-skill")
    candidate_id = encode_candidate_id("local", "skl-1")
    with (
        patch("cubebox.repositories.skill.SkillRepository", return_value=_FakeSkillRepo(skill)),
        patch(
            "cubebox.repositories.skill.SkillVersionRepository",
            return_value=_FakeSkillVersionRepo(None),
        ),
        patch(
            "cubebox.repositories.skill.OrgPreinstalledTombstoneRepository",
            return_value=_FakeTombstoneRepo(None),
        ),
    ):
        tool = create_preview_skill_tool(
            session=_FakeSession(),
            registry=_FakeRegistry(adapter=None),
            catalog=_FakeCatalog("irrelevant"),
            org_id="org-1",
        )
        result = await tool.execute("tc-7", PreviewSkillInput(candidate_id=candidate_id))

    assert result.is_error
    assert "SKILL_VERSION_NOT_FOUND" in result.content[0].text


@pytest.mark.asyncio
async def test_preview_local_tombstoned_preinstalled_returns_not_found() -> None:
    from cubebox.skills.sources.base import encode_candidate_id

    skill = _FakeSkill("skl-1", "1.0.0", source="preinstalled", name="my-skill")
    sv = _FakeSkillVersion("sv-1")
    tombstone = object()  # non-None signals the skill has been tombstoned

    candidate_id = encode_candidate_id("local", "skl-1")
    with (
        patch("cubebox.repositories.skill.SkillRepository", return_value=_FakeSkillRepo(skill)),
        patch(
            "cubebox.repositories.skill.SkillVersionRepository",
            return_value=_FakeSkillVersionRepo(sv),
        ),
        patch(
            "cubebox.repositories.skill.OrgPreinstalledTombstoneRepository",
            return_value=_FakeTombstoneRepo(tombstone),
        ),
    ):
        tool = create_preview_skill_tool(
            session=_FakeSession(),
            registry=_FakeRegistry(adapter=None),
            catalog=_FakeCatalog("# My Skill\nContent."),
            org_id="org-1",
        )
        result = await tool.execute("tc-8", PreviewSkillInput(candidate_id=candidate_id))

    assert result.is_error
    assert "SKILL_NOT_FOUND" in result.content[0].text
