"""Unit tests for ArtifactMiddlewarePi (M3.a.2)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cubepi.agent.types import AgentTool, AgentToolResult
from cubepi.providers.base import AssistantMessage, TextContent, Usage, UserMessage

from cubebox.middleware.artifacts_pi import ArtifactMiddlewarePi, _SaveArtifactArgs

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_middleware(**kwargs: Any) -> ArtifactMiddlewarePi:
    """Build an ArtifactMiddlewarePi with minimal mock dependencies."""
    sandbox = MagicMock()
    defaults = {
        "sandbox": sandbox,
        "conversation_id": "conv-test",
        "org_id": "org-test",
        "workspace_id": "ws-test",
    }
    defaults.update(kwargs)
    return ArtifactMiddlewarePi(**defaults)


def _make_user_msg(text: str = "hello") -> UserMessage:
    return UserMessage(content=[TextContent(text=text)])


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


def test_content_type_registered() -> None:
    """Constructor must register save_artifact → artifact in the tool registry."""
    from cubebox.tools import get_registry

    _make_middleware()
    assert get_registry().get_content_type("save_artifact") == "artifact"


# ---------------------------------------------------------------------------
# transform_context — pass-through cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_messages_returns_empty() -> None:
    mw = _make_middleware()
    with patch.object(mw, "_build_artifact_list", new=AsyncMock(return_value="")):
        out = await mw.transform_context([])
    assert out == []


@pytest.mark.asyncio
async def test_no_user_message_returns_unchanged() -> None:
    mw = _make_middleware()
    msg = AssistantMessage(content=[TextContent(text="hi")], usage=Usage())
    with patch.object(mw, "_build_artifact_list", new=AsyncMock(return_value="")):
        out = await mw.transform_context([msg])
    assert len(out) == 1
    assert out[0] is msg


@pytest.mark.asyncio
async def test_does_not_mutate_original_user_message() -> None:
    mw = _make_middleware()
    msg = _make_user_msg("original text")
    original_text = msg.content[0].text
    with patch.object(mw, "_build_artifact_list", new=AsyncMock(return_value="**list**")):
        await mw.transform_context([msg])
    assert msg.content[0].text == original_text


# ---------------------------------------------------------------------------
# transform_context — injection into last UserMessage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_artifact_prompt_appended_to_last_user_message() -> None:
    mw = _make_middleware()
    msg = _make_user_msg("do something")
    artifact_list = "\n**Existing artifacts:** None yet.\n"
    with patch.object(mw, "_build_artifact_list", new=AsyncMock(return_value=artifact_list)):
        out = await mw.transform_context([msg])
    text = out[0].content[0].text
    assert "do something" in text
    assert "Artifacts" in text  # from ARTIFACT_PROMPT
    assert "save_artifact" in text  # from ARTIFACT_PROMPT


@pytest.mark.asyncio
async def test_artifact_list_appended_when_artifacts_exist() -> None:
    mw = _make_middleware()
    msg = _make_user_msg("hi")
    artifact_list = (
        '\n**Existing artifacts:**\n- id=`art-1` name="site" type=website path=`/out` v1\n'
    )
    with patch.object(mw, "_build_artifact_list", new=AsyncMock(return_value=artifact_list)):
        out = await mw.transform_context([msg])
    text = out[0].content[0].text
    assert "art-1" in text
    assert "site" in text


@pytest.mark.asyncio
async def test_only_last_user_message_is_modified() -> None:
    mw = _make_middleware()
    msg1 = _make_user_msg("first")
    msg2 = _make_user_msg("second")
    artifact_list = "\n**Existing artifacts:** None yet.\n"
    with patch.object(mw, "_build_artifact_list", new=AsyncMock(return_value=artifact_list)):
        out = await mw.transform_context([msg1, msg2])
    assert out[0].content[0].text == "first"
    assert "second" in out[1].content[0].text
    assert "Artifacts" in out[1].content[0].text
    assert "Artifacts" not in out[0].content[0].text


@pytest.mark.asyncio
async def test_assistant_message_between_user_messages_unchanged() -> None:
    mw = _make_middleware()
    user1 = _make_user_msg("first")
    assistant = AssistantMessage(content=[TextContent(text="ok")], usage=Usage())
    user2 = _make_user_msg("second")
    artifact_list = "\n**Existing artifacts:** None yet.\n"
    with patch.object(mw, "_build_artifact_list", new=AsyncMock(return_value=artifact_list)):
        out = await mw.transform_context([user1, assistant, user2])
    assert out[1] is assistant
    assert "second" in out[2].content[0].text
    assert "Artifacts" in out[2].content[0].text


@pytest.mark.asyncio
async def test_user_message_metadata_preserved() -> None:
    mw = _make_middleware()
    msg = UserMessage(
        content=[TextContent(text="hi")],
        metadata={"some_key": "some_value"},
    )
    artifact_list = "\n**Existing artifacts:** None yet.\n"
    with patch.object(mw, "_build_artifact_list", new=AsyncMock(return_value=artifact_list)):
        out = await mw.transform_context([msg])
    assert out[0].metadata.get("some_key") == "some_value"


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

    mw = ArtifactMiddlewarePi(
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
        mw = ArtifactMiddlewarePi(
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
        mw = ArtifactMiddlewarePi(
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
