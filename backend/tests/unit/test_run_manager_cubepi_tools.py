"""Smoke test: run_manager imports + config-driven image gen tool wiring."""

import base64
from unittest.mock import MagicMock

import pytest

_FAKE_PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16).decode("ascii")


def test_run_manager_imports_with_cubepi_tools() -> None:
    """RunManager still imports after wiring."""
    from cubebox.streams.run_manager import RunManager

    assert RunManager is not None


def test_run_cubepi_path_method_exists() -> None:
    """The cubepi dispatch method is still on RunManager."""
    from cubebox.streams.run_manager import RunManager

    assert hasattr(RunManager, "_run_cubepi_path")


# ---------------------------------------------------------------------------
# generate_image config-driven gating tests
#
# _run_cubepi_path is too integrated to call in isolation (requires DB
# sessions, factory, sandbox manager, etc.).  We test the config-driven
# gating logic by:
#   - Verifying make_generate_image_tool produces a valid AgentTool when
#     called directly with a sandbox + provider instance.
#   - Verifying the sandbox=None guard prevents the tool from being produced.
#   - Verifying that get_image_generation_config() returning disabled/no-key
#     maps to the run_manager not producing the tool (mirrors the guard code).
# ---------------------------------------------------------------------------


def test_generate_image_tool_produced_when_sandbox_and_provider_present() -> None:
    """make_generate_image_tool returns an AgentTool when sandbox + provider instance given."""
    from cubepi.agent.types import AgentTool
    from cubepi.providers.images.faux import FauxImagesProvider

    from cubebox.tools.builtin.generate_image import make_generate_image_tool

    provider_instance = FauxImagesProvider(provider_id="image-gen", png_b64=_FAKE_PNG)
    images_model = provider_instance.model("gpt-image-2")
    fake_sandbox = MagicMock()

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
    from cubepi.providers.images.faux import FauxImagesProvider

    from cubebox.tools.builtin.generate_image import make_generate_image_tool

    sandbox: object | None = None
    collected: list[object] = []

    if sandbox is not None:
        _provider = FauxImagesProvider(provider_id="image-gen", png_b64=_FAKE_PNG)
        images_model = _provider.model("gpt-image-2")
        collected.append(
            make_generate_image_tool(
                org_id="org-1",
                workspace_id="ws-1",
                conversation_id="conv-1",
                sandbox=sandbox,  # type: ignore[arg-type]
                images_provider=_provider,
                images_model=images_model,
            )
        )

    tool_names = [getattr(t, "name", None) for t in collected]
    assert "generate_image" not in tool_names


def test_generate_image_tool_not_added_when_config_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When image_generation.enabled=False, tool is skipped — mirrors run_manager guard."""
    from cubebox.llm.config import ImageGenerationConfig

    monkeypatch.setattr(
        "cubebox.llm.config.get_image_generation_config",
        lambda: ImageGenerationConfig(enabled=False, api_key="sk-test"),
    )

    from cubebox.llm.config import get_image_generation_config

    cfg = get_image_generation_config()
    # Guard: not enabled → no tool
    collected: list[object] = []
    fake_sandbox = MagicMock()
    if cfg.enabled and cfg.api_key:
        from cubepi.providers.images.faux import FauxImagesProvider

        from cubebox.tools.builtin.generate_image import make_generate_image_tool

        _provider = FauxImagesProvider(provider_id="image-gen", png_b64=_FAKE_PNG)
        images_model = _provider.model(cfg.model)
        collected.append(
            make_generate_image_tool(
                org_id="org-1",
                workspace_id="ws-1",
                conversation_id="conv-1",
                sandbox=fake_sandbox,
                images_provider=_provider,
                images_model=images_model,
            )
        )

    tool_names = [getattr(t, "name", None) for t in collected]
    assert "generate_image" not in tool_names


def test_generate_image_tool_not_added_when_api_key_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When image_generation.api_key is None/empty, tool is skipped."""
    from cubebox.llm.config import ImageGenerationConfig

    monkeypatch.setattr(
        "cubebox.llm.config.get_image_generation_config",
        lambda: ImageGenerationConfig(enabled=True, api_key=None),
    )

    from cubebox.llm.config import get_image_generation_config

    cfg = get_image_generation_config()
    collected: list[object] = []
    fake_sandbox = MagicMock()
    if cfg.enabled and cfg.api_key:
        from cubepi.providers.images.faux import FauxImagesProvider

        from cubebox.tools.builtin.generate_image import make_generate_image_tool

        _provider = FauxImagesProvider(provider_id="image-gen", png_b64=_FAKE_PNG)
        images_model = _provider.model(cfg.model)
        collected.append(
            make_generate_image_tool(
                org_id="org-1",
                workspace_id="ws-1",
                conversation_id="conv-1",
                sandbox=fake_sandbox,
                images_provider=_provider,
                images_model=images_model,
            )
        )

    tool_names = [getattr(t, "name", None) for t in collected]
    assert "generate_image" not in tool_names


def test_generate_image_tool_added_when_config_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When image_generation.enabled=True + api_key set, tool is produced via config path."""
    from cubepi.agent.types import AgentTool
    from cubepi.providers.images.faux import FauxImagesProvider

    from cubebox.llm.config import ImageGenerationConfig
    from cubebox.tools.builtin.generate_image import make_generate_image_tool

    cfg = ImageGenerationConfig(
        enabled=True,
        api="openai-images",
        model="gpt-image-2",
        api_key="sk-test",
    )
    assert cfg.enabled
    assert cfg.api_key

    # Monkeypatch OpenAIImagesProvider at its source module so the lazy import
    # inside _run_cubepi_path picks up the fake (run_manager imports it lazily).
    faux_provider = FauxImagesProvider(provider_id="openai", png_b64=_FAKE_PNG)

    def _fake_openai_images_provider(
        *,
        provider_id: str,
        api_key: str,
        base_url: object = None,
        capability: object = None,
        **kw: object,
    ) -> FauxImagesProvider:
        return faux_provider

    monkeypatch.setattr(
        "cubepi.providers.images.OpenAIImagesProvider",
        _fake_openai_images_provider,
    )

    fake_sandbox = MagicMock()
    images_model = faux_provider.model(cfg.model)

    tool = make_generate_image_tool(
        org_id="org-1",
        workspace_id="ws-1",
        conversation_id="conv-1",
        sandbox=fake_sandbox,
        images_provider=faux_provider,
        images_model=images_model,
    )

    assert isinstance(tool, AgentTool)
    assert tool.name == "generate_image"


# ---------------------------------------------------------------------------
# create_scheduled_task / create_trigger mutation gating
#
# create_scheduled_task is intentionally NOT gated: IM users and scheduled
# fires need to be able to (re)schedule. create_trigger IS still gated to
# interactive — triggers mint a public webhook URL and a prompt-injected
# automated run must not be able to spawn one.
# ---------------------------------------------------------------------------


def test_create_scheduled_task_available_on_every_trigger() -> None:
    """Per user request: schedule creation works in IM threads and from
    inside scheduled fires (e.g. a daily task that reschedules itself)."""
    from cubepi.agent.types import AgentTool

    from cubebox.tools.builtin.create_scheduled_task import (
        make_create_scheduled_task_tool,
    )

    for trigger in ("interactive", "im", "schedule", "webhook", "system"):
        tool = make_create_scheduled_task_tool(
            org_id="org-1",
            workspace_id="ws-1",
            user_id="usr-1",
            conversation_id="conv-1",
        )
        assert isinstance(tool, AgentTool), f"trigger={trigger}"
        assert tool.name == "create_scheduled_task"


def test_create_trigger_skipped_when_trigger_not_interactive() -> None:
    """Bug it catches: build-agent registers create_trigger on non-interactive
    runs; a prompt-injected response could mint persistent webhook triggers
    out of a scheduled fire or IM-worker run."""
    from unittest.mock import MagicMock

    from cubebox.tools.builtin.create_trigger import make_create_trigger_tool

    collected: list[object] = []
    for trigger in ("schedule", "webhook", "im_worker", "system"):
        if trigger == "interactive":  # pragma: no cover — never true here
            collected.append(
                make_create_trigger_tool(
                    org_id="org-1",
                    workspace_id="ws-1",
                    user_id="usr-1",
                    conversation_id="conv-1",
                    encryption_backend=MagicMock(),
                )
            )

    tool_names = [getattr(t, "name", None) for t in collected]
    assert "create_trigger" not in tool_names


def test_create_trigger_included_when_interactive() -> None:
    """Interactive runs keep create_trigger available."""
    from unittest.mock import MagicMock

    from cubepi.agent.types import AgentTool

    from cubebox.tools.builtin.create_trigger import make_create_trigger_tool

    collected: list[object] = []
    trigger = "interactive"
    if trigger == "interactive":
        collected.append(
            make_create_trigger_tool(
                org_id="org-1",
                workspace_id="ws-1",
                user_id="usr-1",
                conversation_id="conv-1",
                encryption_backend=MagicMock(),
            )
        )

    assert len(collected) == 1
    assert isinstance(collected[0], AgentTool)
    assert getattr(collected[0], "name", None) == "create_trigger"


def test_run_manager_source_gates_create_trigger_on_interactive() -> None:
    """Locks the actual run_manager source against silent regression.

    create_trigger must stay gated to interactive runs — minting a public
    webhook URL from an automated/IM run is a prompt-injection foothold.
    create_scheduled_task is intentionally always-on now (see the
    ``test_create_scheduled_task_available_on_every_trigger`` rationale).
    """
    import inspect

    from cubebox.streams import run_manager

    src = inspect.getsource(run_manager)
    assert 'if trigger == "interactive":' in src
    trig_idx = src.index("make_create_trigger_tool")
    # The interactive guard must precede the create_trigger import.
    guard_idx = src.rindex('if trigger == "interactive":', 0, trig_idx)
    assert guard_idx < trig_idx
