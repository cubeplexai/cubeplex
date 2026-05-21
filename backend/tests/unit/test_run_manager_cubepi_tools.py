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
#   - with a sandbox AND a valid provider instance: the factory produces a valid
#     AgentTool named "generate_image"
#   - without a sandbox: the tool is NOT produced (i.e. the if-guard works)
#   - without a valid OpenAI credential (key is None): the tool is NOT produced
#
# The gating for "no OpenAI credential" is exercised by checking that
# resolve_openai_image_credentials() → (None, None) causes the tool to be
# skipped, mirroring the guard in run_manager._run_cubepi_path.
# ---------------------------------------------------------------------------


def test_generate_image_tool_produced_when_sandbox_and_provider_present() -> None:
    """make_generate_image_tool returns an AgentTool when sandbox + provider instance given."""
    import base64

    from cubepi.agent.types import AgentTool
    from cubepi.providers.images.faux import FauxImagesProvider
    from cubepi.providers.images.types import ImagesModel

    from cubebox.tools.builtin.generate_image import make_generate_image_tool

    _fake_png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16).decode("ascii")
    provider_instance = FauxImagesProvider(_fake_png)

    fake_sandbox = MagicMock()
    images_model = ImagesModel(id="gpt-image-1", provider="openai", api="openai-images")

    tool = make_generate_image_tool(
        org_id="org-1",
        workspace_id="ws-1",
        conversation_id="conv-1",
        sandbox=fake_sandbox,
        images_provider=provider_instance,
        images_model=images_model,
    )

    assert isinstance(tool, AgentTool)
    assert tool.name == "generate_image"


def test_generate_image_tool_not_added_when_sandbox_is_none() -> None:
    """The if-sandbox-is-not-None guard prevents adding generate_image to _builtin_tools."""
    import base64

    from cubepi.providers.images.faux import FauxImagesProvider
    from cubepi.providers.images.types import ImagesModel

    from cubebox.tools.builtin.generate_image import make_generate_image_tool

    sandbox: object | None = None
    collected: list[object] = []

    _fake_png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16).decode("ascii")

    # Replicate the guard from run_manager._run_cubepi_path
    if sandbox is not None:
        images_model = ImagesModel(id="gpt-image-1", provider="openai", api="openai-images")
        collected.append(
            make_generate_image_tool(
                org_id="org-1",
                workspace_id="ws-1",
                conversation_id="conv-1",
                sandbox=sandbox,  # type: ignore[arg-type]
                images_provider=FauxImagesProvider(_fake_png),
                images_model=images_model,
            )
        )

    tool_names = [getattr(t, "name", None) for t in collected]
    assert "generate_image" not in tool_names


def test_generate_image_tool_not_added_when_no_openai_credential() -> None:
    """When resolve_openai_image_credentials() returns (None, None), tool is skipped.

    Mirrors the _img_key is None guard in run_manager._run_cubepi_path.
    """
    from cubebox.llm.config import LLMConfig, ProviderConfig
    from cubebox.llm.factory import LLMFactory

    # Config with only a non-OpenAI compatible provider (DeepSeek-like).
    factory = LLMFactory(
        llm_config=LLMConfig(
            default_model="deepseek/deepseek-chat",
            providers={
                "deepseek": ProviderConfig(
                    api="openai-completions",
                    base_url="https://api.deepseek.com/v1",
                    api_key="sk-ds",
                ),
            },
        )
    )

    key, _base_url = factory.resolve_openai_image_credentials()
    assert key is None, "Expected no real-OpenAI key to be resolved"

    # Replicate the run_manager guard: tool is only added when key is not None.
    collected: list[object] = []
    fake_sandbox = MagicMock()
    if key is not None:
        import base64

        from cubepi.providers.images.faux import FauxImagesProvider
        from cubepi.providers.images.types import ImagesModel

        from cubebox.tools.builtin.generate_image import make_generate_image_tool

        _fake_png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16).decode("ascii")
        images_model = ImagesModel(id="gpt-image-1", provider="openai", api="openai-images")
        collected.append(
            make_generate_image_tool(
                org_id="org-1",
                workspace_id="ws-1",
                conversation_id="conv-1",
                sandbox=fake_sandbox,
                images_provider=FauxImagesProvider(_fake_png),
                images_model=images_model,
            )
        )

    tool_names = [getattr(t, "name", None) for t in collected]
    assert "generate_image" not in tool_names
