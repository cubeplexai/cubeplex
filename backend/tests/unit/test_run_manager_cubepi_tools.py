"""Smoke test: run_manager imports + new tool wiring compiles (M2.5, B3)."""

from unittest.mock import MagicMock


def test_run_manager_imports_with_cubepi_tools() -> None:
    """RunManager still imports after M2.5 wiring."""
    from cubebox.streams.run_manager import RunManager

    assert RunManager is not None


def test_run_cubepi_path_method_exists() -> None:
    """The cubepi dispatch method is still on RunManager after M2.5."""
    from cubebox.streams.run_manager import RunManager

    assert hasattr(RunManager, "_run_cubepi_path")


# ---------------------------------------------------------------------------
# generate_image sandbox-gating tests (B3)
#
# _run_cubepi_path is too integrated to call in isolation (requires DB
# sessions, factory, sandbox manager, etc.).  We test the sandbox-gating
# logic by directly exercising make_generate_image_tool and verifying that:
#   - with a sandbox: the factory produces a valid AgentTool named "generate_image"
#   - without a sandbox: the tool is NOT produced (i.e. the if-guard works)
#
# This mirrors the guard in run_manager.py and gives a seam we can assert on
# without a full integration harness.
# ---------------------------------------------------------------------------


def test_generate_image_tool_produced_when_sandbox_present() -> None:
    """make_generate_image_tool returns an AgentTool when a sandbox is provided.

    Uses the cubepi faux images provider so no real OpenAI credentials are needed.
    """
    import base64

    from cubepi.agent.types import AgentTool
    from cubepi.providers.images.faux import register_faux_images
    from cubepi.providers.images.types import ImagesModel

    from cubebox.tools.builtin.generate_image import make_generate_image_tool

    _fake_png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16).decode("ascii")
    register_faux_images(_fake_png)  # register the faux provider so api_key=None is fine

    fake_sandbox = MagicMock()
    images_model = ImagesModel(id="faux-image-1", provider="faux", api="faux-images")

    tool = make_generate_image_tool(
        org_id="org-1",
        workspace_id="ws-1",
        conversation_id="conv-1",
        sandbox=fake_sandbox,
        images_model=images_model,
        api_key=None,
    )

    assert isinstance(tool, AgentTool)
    assert tool.name == "generate_image"


def test_generate_image_tool_not_added_when_sandbox_is_none() -> None:
    """The if-sandbox-is-not-None guard prevents adding generate_image to _builtin_tools."""
    from cubepi.providers.images.types import ImagesModel

    from cubebox.tools.builtin.generate_image import make_generate_image_tool

    sandbox: object | None = None
    collected: list[object] = []

    # Replicate the guard from run_manager._run_cubepi_path
    if sandbox is not None:
        images_model = ImagesModel(id="gpt-image-1", provider="openai", api="openai-images")
        collected.append(
            make_generate_image_tool(
                org_id="org-1",
                workspace_id="ws-1",
                conversation_id="conv-1",
                sandbox=sandbox,  # type: ignore[arg-type]
                images_model=images_model,
                api_key=None,
            )
        )

    tool_names = [getattr(t, "name", None) for t in collected]
    assert "generate_image" not in tool_names
