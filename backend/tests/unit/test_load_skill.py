"""load_skill tool ported to cubepi (M2.3) — unit tests."""

from __future__ import annotations

import json

import pytest

from cubeplex.tools.builtin.load_skill import create_load_skill_tool

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeResolvedSkill:
    """Minimal stand-in for cubeplex.skills.service.ResolvedSkill."""

    def __init__(self, name: str, version: str, skill_version_id: str, content: str) -> None:
        self.name = name
        self.version = version
        self.skill_version_id = skill_version_id
        self._content = content


class _FakeCatalog:
    """Test double for SkillCatalogService.

    Supports find_enabled_by_name and fetch_skill_md with configurable
    failure modes for the fetch step.
    """

    def __init__(
        self,
        skills: dict[str, tuple[str, str]],  # name → (version, content)
        *,
        fetch_raises: Exception | None = None,
    ) -> None:
        self._skills = skills
        self._fetch_raises = fetch_raises

    async def find_enabled_by_name(
        self, workspace_id: str, *, org_id: str, name: str
    ) -> _FakeResolvedSkill | None:
        entry = self._skills.get(name)
        if entry is None:
            return None
        version, content = entry
        return _FakeResolvedSkill(
            name=name,
            version=version,
            skill_version_id=f"sv-{name}",
            content=content,
        )

    async def fetch_skill_md(self, skill_version_id: str) -> str:
        if self._fetch_raises is not None:
            raise self._fetch_raises
        # Look up by sv-<name> convention used in find_enabled_by_name
        skill_name = skill_version_id.removeprefix("sv-")
        entry = self._skills.get(skill_name)
        if entry is None:
            raise ValueError(f"skill_version_id not found: {skill_version_id}")
        _, content = entry
        return content


@pytest.fixture
def catalog() -> _FakeCatalog:
    return _FakeCatalog(
        {
            "writing": ("1.0.0", "Write clearly.\nUse short sentences."),
            "math": ("2.1.0", "Show your work."),
        }
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_skill_returns_content(catalog: _FakeCatalog) -> None:
    tool = create_load_skill_tool(catalog=catalog, workspace_id="ws-1", org_id="org-1")
    args = tool.parameters(skill_name="writing")
    result = await tool.execute("tc-1", args, signal=None, on_update=None)
    assert result.is_error in (None, False)
    payload = json.loads(result.content[0].text)
    assert payload["loaded"] is True
    assert payload["content"] == "Write clearly.\nUse short sentences."
    assert payload["version"] == "1.0.0"
    assert payload["skill_name"] == "writing"
    assert payload["error"] is None


@pytest.mark.asyncio
async def test_load_skill_returns_sandbox_path(catalog: _FakeCatalog) -> None:
    # The agent must be handed the exact sandbox dir for sibling files instead
    # of guessing it from the name.
    tool = create_load_skill_tool(catalog=catalog, workspace_id="ws-1", org_id="org-1")
    result = await tool.execute(
        "tc-1", tool.parameters(skill_name="writing"), signal=None, on_update=None
    )
    payload = json.loads(result.content[0].text)
    assert payload["path"] == "/workspace/.skills/writing/1.0.0"


@pytest.mark.asyncio
async def test_load_skill_path_normalises_colon_name() -> None:
    # Registry canonical name <org>:<skill> — the returned path must not carry
    # the path-hostile colon that broke bundled-file reads.
    catalog = _FakeCatalog({"acme:designer": ("3.0.0", "Design boldly.")})
    tool = create_load_skill_tool(catalog=catalog, workspace_id="ws-1", org_id="org-1")
    result = await tool.execute(
        "tc-c", tool.parameters(skill_name="acme:designer"), signal=None, on_update=None
    )
    payload = json.loads(result.content[0].text)
    assert payload["path"] == "/workspace/.skills/acme__designer/3.0.0"
    assert ":" not in payload["path"]


@pytest.mark.asyncio
async def test_load_skill_second_skill_returns_content(catalog: _FakeCatalog) -> None:
    tool = create_load_skill_tool(catalog=catalog, workspace_id="ws-1", org_id="org-1")
    args = tool.parameters(skill_name="math")
    result = await tool.execute("tc-2", args, signal=None, on_update=None)
    assert result.is_error in (None, False)
    payload = json.loads(result.content[0].text)
    assert payload["loaded"] is True
    assert payload["content"] == "Show your work."
    assert payload["version"] == "2.1.0"


@pytest.mark.asyncio
async def test_load_skill_missing_returns_error(catalog: _FakeCatalog) -> None:
    tool = create_load_skill_tool(catalog=catalog, workspace_id="ws-1", org_id="org-1")
    args = tool.parameters(skill_name="nonexistent")
    result = await tool.execute("tc-3", args, signal=None, on_update=None)
    assert result.is_error is True
    payload = json.loads(result.content[0].text)
    assert payload["loaded"] is False
    assert "not enabled" in payload["error"].lower() or "not found" in payload["error"].lower()
    assert payload["skill_name"] == "nonexistent"


@pytest.mark.asyncio
async def test_load_skill_fetch_failure_returns_error() -> None:
    catalog = _FakeCatalog(
        {"writing": ("1.0.0", "Write clearly.")},
        fetch_raises=OSError("object store unavailable"),
    )
    tool = create_load_skill_tool(catalog=catalog, workspace_id="ws-1", org_id="org-1")
    args = tool.parameters(skill_name="writing")
    result = await tool.execute("tc-4", args, signal=None, on_update=None)
    assert result.is_error is True
    payload = json.loads(result.content[0].text)
    assert payload["loaded"] is False
    assert "object store unavailable" in payload["error"]


def test_load_skill_tool_metadata() -> None:
    tool = create_load_skill_tool(catalog=_FakeCatalog({}), workspace_id="ws-1", org_id="org-1")
    assert tool.name == "load_skill"
    assert tool.description
    assert "skill" in tool.description.lower()


def test_load_skill_tool_parameters_schema() -> None:
    from pydantic import BaseModel

    from cubeplex.tools.builtin.load_skill import LoadSkillInput

    tool = create_load_skill_tool(catalog=_FakeCatalog({}), workspace_id="ws-1", org_id="org-1")
    assert issubclass(tool.parameters, BaseModel)
    assert tool.parameters is LoadSkillInput
