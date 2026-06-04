"""Unit tests for ArtifactMiddleware (M3.a.2)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cubepi.agent.types import AgentTool, AgentToolResult
from cubepi.providers.base import TextContent

from cubebox.middleware.artifacts import ArtifactMiddleware, _SaveArtifactArgs

# ---------------------------------------------------------------------------
# Helpers for transform_system_prompt tests
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_middleware(**kwargs: Any) -> ArtifactMiddleware:
    """Build an ArtifactMiddleware with minimal mock dependencies."""
    sandbox = MagicMock()
    defaults = {
        "sandbox": sandbox,
        "conversation_id": "conv-test",
        "org_id": "org-test",
        "workspace_id": "ws-test",
    }
    defaults.update(kwargs)
    return ArtifactMiddleware(**defaults)


# ---------------------------------------------------------------------------
# tools property
# ---------------------------------------------------------------------------


def test_tools_returns_list_of_agent_tools() -> None:
    mw = _make_middleware()
    tools = mw.tools
    assert isinstance(tools, list)
    assert len(tools) == 1
    tool = tools[0]
    assert isinstance(tool, AgentTool)


def test_tool_has_correct_name() -> None:
    mw = _make_middleware()
    assert mw.tools[0].name == "save_artifact"


def test_tool_has_correct_parameters_schema() -> None:
    mw = _make_middleware()
    tool = mw.tools[0]
    assert tool.parameters is _SaveArtifactArgs
    # Verify schema can be serialised (pydantic model)
    schema = tool.parameters.model_json_schema()
    assert "properties" in schema
    required_fields = {"name", "artifact_type", "path"}
    assert required_fields.issubset(schema["properties"].keys())


def test_tool_execute_is_callable() -> None:
    mw = _make_middleware()
    assert callable(mw.tools[0].execute)


def test_tool_description_mentions_save_artifact() -> None:
    mw = _make_middleware()
    desc = mw.tools[0].description
    assert "artifact" in desc.lower()
    assert "sandbox" in desc.lower()


def test_tools_property_returns_fresh_list_each_time() -> None:
    mw = _make_middleware()
    assert mw.tools is not mw.tools  # new list each call, same tool object
    assert mw.tools[0] is mw.tools[0]


# ---------------------------------------------------------------------------
# transform_system_prompt — appends artifact section to system prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_artifact_prompt_appended_to_system_prompt() -> None:
    mw = _make_middleware()
    artifact_list = "\n**Existing artifacts:** None yet.\n"
    with patch.object(mw, "_build_artifact_list", new=AsyncMock(return_value=artifact_list)):
        out = await mw.transform_system_prompt("Base system prompt.", ctx=object(), signal=None)
    assert "Base system prompt." in out
    assert "Artifacts" in out  # from ARTIFACT_PROMPT
    assert "save_artifact" in out  # from ARTIFACT_PROMPT
    assert "None yet" in out


@pytest.mark.asyncio
async def test_artifact_list_appended_when_artifacts_exist() -> None:
    mw = _make_middleware()
    artifact_list = (
        '\n**Existing artifacts:**\n- id=`art-1` name="site" type=website path=`/out` v1\n'
    )
    with patch.object(mw, "_build_artifact_list", new=AsyncMock(return_value=artifact_list)):
        out = await mw.transform_system_prompt("System.", ctx=object(), signal=None)
    assert "art-1" in out
    assert "site" in out


@pytest.mark.asyncio
async def test_empty_system_prompt_still_injects() -> None:
    mw = _make_middleware()
    artifact_list = "\n**Existing artifacts:** None yet.\n"
    with patch.object(mw, "_build_artifact_list", new=AsyncMock(return_value=artifact_list)):
        out = await mw.transform_system_prompt("", ctx=object(), signal=None)
    assert "Artifacts" in out


@pytest.mark.asyncio
async def test_transform_system_prompt_does_not_mutate_input() -> None:
    mw = _make_middleware()
    original = "Original prompt."
    with patch.object(mw, "_build_artifact_list", new=AsyncMock(return_value="**list**")):
        out = await mw.transform_system_prompt(original, ctx=object(), signal=None)
    # Original string is immutable in Python; just verify return is different
    assert out != original
    assert original in out


@pytest.mark.asyncio
async def test_transform_system_prompt_returns_string() -> None:
    mw = _make_middleware()
    with patch.object(mw, "_build_artifact_list", new=AsyncMock(return_value="")):
        out = await mw.transform_system_prompt("prompt", ctx=object(), signal=None)
    assert isinstance(out, str)


# ---------------------------------------------------------------------------
# tool execute — success and error paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_execute_path_not_found_returns_error() -> None:
    """When the sandbox reports the path does not exist, return error."""
    sandbox = MagicMock()
    exec_result = MagicMock()
    exec_result.exit_code = 1
    sandbox.execute = AsyncMock(return_value=exec_result)

    mw = ArtifactMiddleware(
        sandbox=sandbox,
        conversation_id="conv-1",
        org_id="org-1",
        workspace_id="ws-1",
    )
    tool = mw.tools[0]
    args = _SaveArtifactArgs(
        name="test",
        artifact_type="file",
        path="/nonexistent",
    )
    result = await tool.execute("tc-1", args, signal=None, on_update=None)
    assert isinstance(result, AgentToolResult)
    assert result.is_error is True
    payload = json_loads_content(result)
    assert "error" in payload
    assert "/nonexistent" in payload["error"]


@pytest.mark.asyncio
async def test_tool_execute_creates_artifact_in_db() -> None:
    """Happy-path: sandbox says path exists, DB create is called, result returned."""
    sandbox = MagicMock()
    exec_result = MagicMock()
    exec_result.exit_code = 0
    sandbox.execute = AsyncMock(return_value=exec_result)

    # Build a fake artifact object that repo.create returns
    fake_artifact = MagicMock()
    fake_artifact.id = "art-abc"
    fake_artifact.name = "My Site"
    fake_artifact.artifact_type = "website"
    fake_artifact.version = 1
    fake_artifact.to_dict.return_value = {
        "id": "art-abc",
        "name": "My Site",
        "artifact_type": "website",
        "version": 1,
    }

    mock_repo = AsyncMock()
    mock_repo.find_by_path = AsyncMock(return_value=None)  # no existing artifact
    mock_repo.create = AsyncMock(return_value=fake_artifact)

    mock_version_repo = AsyncMock()
    mock_version_repo.create = AsyncMock()

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    # The lazy imports inside _execute resolve through the original modules;
    # patch at the source locations used by those lazy imports.
    with (
        patch("cubebox.db.engine.async_session_maker", return_value=mock_session),
        patch("cubebox.repositories.ArtifactRepository", return_value=mock_repo),
        patch(
            "cubebox.repositories.ArtifactVersionRepository",
            return_value=mock_version_repo,
        ),
        patch(
            "cubebox.objectstore.get_objectstore_client",
            side_effect=RuntimeError("no objectstore in unit test"),
        ),
    ):
        mw = ArtifactMiddleware(
            sandbox=sandbox,
            conversation_id="conv-1",
            org_id="org-1",
            workspace_id="ws-1",
        )
        tool = mw.tools[0]
        args = _SaveArtifactArgs(
            name="My Site",
            artifact_type="website",
            path="/out/index.html",
            entry_file=None,
        )
        result = await tool.execute("tc-2", args, signal=None, on_update=None)

    assert isinstance(result, AgentToolResult)
    assert result.is_error is None or not result.is_error
    payload = json_loads_content(result)
    assert payload["action"] == "created"
    assert payload["artifact"]["id"] == "art-abc"


@pytest.mark.asyncio
async def test_tool_execute_updates_existing_artifact() -> None:
    """When artifact_id is supplied, repo.update is called instead of create."""
    sandbox = MagicMock()
    exec_result = MagicMock()
    exec_result.exit_code = 0
    sandbox.execute = AsyncMock(return_value=exec_result)

    fake_artifact = MagicMock()
    fake_artifact.id = "art-xyz"
    fake_artifact.name = "Updated Site"
    fake_artifact.artifact_type = "website"
    fake_artifact.version = 2
    fake_artifact.to_dict.return_value = {
        "id": "art-xyz",
        "name": "Updated Site",
        "artifact_type": "website",
        "version": 2,
    }

    mock_repo = AsyncMock()
    mock_repo.update = AsyncMock(return_value=fake_artifact)

    mock_version_repo = AsyncMock()
    mock_version_repo.create = AsyncMock()

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("cubebox.db.engine.async_session_maker", return_value=mock_session),
        patch("cubebox.repositories.ArtifactRepository", return_value=mock_repo),
        patch(
            "cubebox.repositories.ArtifactVersionRepository",
            return_value=mock_version_repo,
        ),
        patch(
            "cubebox.objectstore.get_objectstore_client",
            side_effect=RuntimeError("no objectstore"),
        ),
    ):
        mw = ArtifactMiddleware(
            sandbox=sandbox,
            conversation_id="conv-1",
            org_id="org-1",
            workspace_id="ws-1",
        )
        args = _SaveArtifactArgs(
            name="Updated Site",
            artifact_type="website",
            path="/out/index.html",
            artifact_id="art-xyz",
        )
        result = await mw.tools[0].execute("tc-3", args)

    payload = json_loads_content(result)
    assert payload["action"] == "updated"
    assert payload["artifact"]["version"] == 2
    mock_repo.update.assert_called_once()
    # create should NOT have been called
    assert not mock_repo.create.called


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def json_loads_content(result: AgentToolResult) -> dict:  # type: ignore[type-arg]
    """Extract and parse the JSON payload from an AgentToolResult."""
    import json

    text_blocks = [c for c in result.content if isinstance(c, TextContent)]
    assert text_blocks, "Expected at least one TextContent in result"
    return json.loads(text_blocks[0].text)
