"""Unit tests for the generate_image tool (cubepi.AgentTool).

Hermetic: no DB, no Pillow, no real image provider.
- Provider instances are passed directly to make_generate_image_tool (DI).
- register_artifact_from_sandbox and resize_to_long_edge are monkeypatched.
"""

from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any

import pytest
from cubepi.providers.base import ImageContent
from cubepi.providers.images import AssistantImages, ImagesContext, ImagesModel, ImagesOptions

from cubeplex.sandbox.base import ExecuteResult
from cubeplex.tools.builtin.generate_image import GenerateImageInput, make_generate_image_tool

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

_FAKE_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
_FAKE_PNG_B64 = base64.b64encode(_FAKE_PNG_BYTES).decode("ascii")
_FAKE_MODEL = ImagesModel(id="gpt-image-1", provider_id="openai", api="openai-images")


# ---------------------------------------------------------------------------
# Fake sandbox
# ---------------------------------------------------------------------------


class FakeSandbox:
    """Minimal sandbox stub: records uploads; returns base64 for base64 -w0 commands."""

    def __init__(self, *, base64_for_paths: dict[str, str] | None = None) -> None:
        # path -> base64 content to return for `base64 -w0 <path>` commands
        self._base64_for_paths: dict[str, str] = base64_for_paths or {}
        self.uploaded: list[tuple[str, bytes]] = []

    async def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResult:
        # Simulate `base64 -w0 <path>` by extracting the path via shlex
        if "base64 -w0" in command:
            import shlex

            parts = shlex.split(command)
            # parts[-1] is the path
            path = parts[-1]
            if path in self._base64_for_paths:
                return ExecuteResult(output=self._base64_for_paths[path], exit_code=0)
            return ExecuteResult(output="", exit_code=1)
        # Fallback: succeed with no output
        return ExecuteResult(output="", exit_code=0)

    async def upload(self, files: list[tuple[str, bytes]]) -> None:
        self.uploaded.extend(files)

    # Unused abstract methods — satisfy the type system in tests
    @property
    def id(self) -> str:
        return "fake-sandbox"

    @property
    def workdir(self) -> str:
        return "/work"

    async def download(self, paths: list[str]) -> list[tuple[str, bytes]]:
        return []

    async def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_artifact(*, art_id: str = "art_1", version: int = 1) -> SimpleNamespace:
    ns = SimpleNamespace(id=art_id, version=version, name="image.png")
    ns.to_dict = lambda: {"id": art_id, "version": version, "name": "image.png"}  # type: ignore[attr-defined]
    return ns


def _make_faux_provider(png_b64: str = _FAKE_PNG_B64) -> Any:
    """Return a FauxImagesProvider instance (no global registry side-effects)."""
    from cubepi.providers.images.faux import FauxImagesProvider

    return FauxImagesProvider(provider_id="faux", png_b64=png_b64)


# ---------------------------------------------------------------------------
# Test 1: success path — fresh prompt (no edit_source_paths)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_image_success_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path: faux provider returns one PNG; artifact is registered; small JPEG returned."""
    sandbox = FakeSandbox()

    artifact_kwargs_captured: dict[str, Any] = {}

    async def fake_register(**kwargs: Any) -> SimpleNamespace:
        artifact_kwargs_captured.update(kwargs)
        return _make_artifact()

    monkeypatch.setattr(
        "cubeplex.tools.builtin.generate_image.register_artifact_from_sandbox",
        fake_register,
    )
    monkeypatch.setattr(
        "cubeplex.tools.builtin.generate_image.resize_to_long_edge",
        lambda data, *, target, jpeg_quality: b"SMALLJPEG",
    )

    tool = make_generate_image_tool(
        org_id="org-1",
        workspace_id="ws-1",
        conversation_id="conv-1",
        sandbox=sandbox,  # type: ignore[arg-type]
        images_provider=_make_faux_provider(),
        images_model=_FAKE_MODEL,
    )

    args = GenerateImageInput(prompt="a cat")
    result = await tool.execute("tc-1", args, signal=None, on_update=None)

    # Not an error (is_error is None when unset, falsy either way)
    assert not result.is_error

    # Sandbox upload was called for the target path
    assert len(sandbox.uploaded) == 1
    uploaded_path, uploaded_bytes = sandbox.uploaded[0]
    assert uploaded_path.startswith("/work/")
    assert uploaded_path.endswith(".png")
    assert uploaded_bytes == _FAKE_PNG_BYTES

    # Content: TextContent + ImageContent
    text_blocks = [c for c in result.content if hasattr(c, "text")]
    image_blocks = [c for c in result.content if hasattr(c, "source")]
    assert len(text_blocks) >= 1
    assert len(image_blocks) == 1

    img = image_blocks[0]
    assert img.media_type == "image/jpeg"
    assert base64.b64decode(img.source) == b"SMALLJPEG"

    # Artifact registered with correct type
    assert artifact_kwargs_captured["artifact_type"] == "image"
    assert artifact_kwargs_captured["conversation_id"] == "conv-1"
    assert artifact_kwargs_captured["org_id"] == "org-1"
    assert artifact_kwargs_captured["workspace_id"] == "ws-1"

    # Returned text is JSON with artifact dict
    import json

    parsed = json.loads(text_blocks[0].text)
    assert parsed["action"] == "created"
    assert parsed["artifact"]["id"] == "art_1"
    assert parsed["artifact"]["version"] == 1


# ---------------------------------------------------------------------------
# Test 2: provider error → no artifact registered
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_image_provider_error_no_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the provider raises ProviderError, result is is_error=True, no artifact."""
    from cubepi.errors import ProviderError

    class _ErrorProvider:
        async def generate_images(
            self,
            model: ImagesModel,
            context: ImagesContext,
            *,
            options: ImagesOptions | None = None,
        ) -> AssistantImages:
            raise ProviderError("policy")

    sandbox = FakeSandbox()
    artifact_call_count = 0

    async def counting_register(**kwargs: Any) -> SimpleNamespace:
        nonlocal artifact_call_count
        artifact_call_count += 1
        return _make_artifact()

    monkeypatch.setattr(
        "cubeplex.tools.builtin.generate_image.register_artifact_from_sandbox",
        counting_register,
    )

    tool = make_generate_image_tool(
        org_id="org-1",
        workspace_id="ws-1",
        conversation_id="conv-1",
        sandbox=sandbox,  # type: ignore[arg-type]
        images_provider=_ErrorProvider(),
        images_model=_FAKE_MODEL,
    )

    args = GenerateImageInput(prompt="bad prompt")
    result = await tool.execute("tc-2", args, signal=None, on_update=None)

    assert result.is_error is True
    assert artifact_call_count == 0
    error_text = " ".join(c.text for c in result.content if hasattr(c, "text"))
    assert "policy" in error_text or "failed" in error_text.lower()


# ---------------------------------------------------------------------------
# Test 3: edit branch — write-back to source path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_image_edit_branch_writes_to_source_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With edit_source_paths=['/work/src.png'], the output is written to that path."""
    source_path = "/work/src.png"
    sandbox = FakeSandbox(base64_for_paths={source_path: _FAKE_PNG_B64})

    artifact_kwargs_captured: dict[str, Any] = {}

    async def fake_register(**kwargs: Any) -> SimpleNamespace:
        artifact_kwargs_captured.update(kwargs)
        return _make_artifact(art_id="art_edit", version=2)

    monkeypatch.setattr(
        "cubeplex.tools.builtin.generate_image.register_artifact_from_sandbox",
        fake_register,
    )
    monkeypatch.setattr(
        "cubeplex.tools.builtin.generate_image.resize_to_long_edge",
        lambda data, *, target, jpeg_quality: b"EDITJPEG",
    )

    tool = make_generate_image_tool(
        org_id="org-1",
        workspace_id="ws-1",
        conversation_id="conv-1",
        sandbox=sandbox,  # type: ignore[arg-type]
        images_provider=_make_faux_provider(),
        images_model=_FAKE_MODEL,
    )

    args = GenerateImageInput(prompt="cat with hat", edit_source_paths=[source_path])
    result = await tool.execute("tc-3", args, signal=None, on_update=None)

    assert not result.is_error

    # Upload target == source path (version-bump semantics)
    assert len(sandbox.uploaded) == 1
    uploaded_path, _ = sandbox.uploaded[0]
    assert uploaded_path == source_path

    # Artifact path matches the source path
    assert artifact_kwargs_captured["path"] == source_path

    # Return contains the small JPEG
    image_blocks = [c for c in result.content if hasattr(c, "source")]
    assert len(image_blocks) == 1
    assert base64.b64decode(image_blocks[0].source) == b"EDITJPEG"


# ---------------------------------------------------------------------------
# Test 4: edit source unreadable → fail fast (is_error, no artifact)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_image_unreadable_edit_source_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing/unreadable edit source must error out, not silently fresh-generate."""
    # FakeSandbox returns exit_code 1 for any path not in base64_for_paths.
    sandbox = FakeSandbox()
    artifact_call_count = 0

    async def counting_register(**kwargs: Any) -> SimpleNamespace:
        nonlocal artifact_call_count
        artifact_call_count += 1
        return _make_artifact()

    monkeypatch.setattr(
        "cubeplex.tools.builtin.generate_image.register_artifact_from_sandbox",
        counting_register,
    )

    tool = make_generate_image_tool(
        org_id="org-1",
        workspace_id="ws-1",
        conversation_id="conv-1",
        sandbox=sandbox,  # type: ignore[arg-type]
        images_provider=_make_faux_provider(),
        images_model=_FAKE_MODEL,
    )

    args = GenerateImageInput(prompt="recolor", edit_source_paths=["/work/missing.png"])
    result = await tool.execute("tc-4", args, signal=None, on_update=None)

    assert result.is_error is True
    assert artifact_call_count == 0
    assert len(sandbox.uploaded) == 0
    error_text = " ".join(c.text for c in result.content if hasattr(c, "text"))
    assert "/work/missing.png" in error_text


# ---------------------------------------------------------------------------
# Test 5: size/quality flow through options to the provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_image_size_quality_passed_via_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """size and quality args must flow through ImagesContext to the provider."""
    captured_contexts: list[ImagesContext] = []

    class _SpyProvider:
        async def generate_images(
            self,
            model: ImagesModel,
            context: ImagesContext,
            *,
            options: ImagesOptions | None = None,
        ) -> AssistantImages:
            captured_contexts.append(context)
            return AssistantImages(
                api=model.api,
                provider_id=model.provider_id,
                model=model.id,
                output=[ImageContent(source=_FAKE_PNG_B64, media_type="image/png")],
                stop_reason="stop",
            )

    sandbox = FakeSandbox()

    async def fake_register(**kwargs: Any) -> SimpleNamespace:
        return _make_artifact()

    monkeypatch.setattr(
        "cubeplex.tools.builtin.generate_image.register_artifact_from_sandbox",
        fake_register,
    )
    monkeypatch.setattr(
        "cubeplex.tools.builtin.generate_image.resize_to_long_edge",
        lambda data, *, target, jpeg_quality: b"SMALL",
    )

    tool = make_generate_image_tool(
        org_id="org-1",
        workspace_id="ws-1",
        conversation_id="conv-1",
        sandbox=sandbox,  # type: ignore[arg-type]
        images_provider=_SpyProvider(),
        images_model=_FAKE_MODEL,
    )

    args = GenerateImageInput(prompt="landscape", size="1536x864", quality="high")
    result = await tool.execute("tc-5", args, signal=None, on_update=None)

    assert not result.is_error
    assert len(captured_contexts) == 1
    ctx = captured_contexts[0]
    assert ctx.size == "1536x864"
    assert ctx.quality == "high"


@pytest.mark.asyncio
async def test_generate_image_no_options_when_size_quality_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When size/quality are omitted, ImagesContext carries None for those fields."""
    captured_contexts: list[ImagesContext] = []

    class _SpyProvider2:
        async def generate_images(
            self,
            model: ImagesModel,
            context: ImagesContext,
            *,
            options: ImagesOptions | None = None,
        ) -> AssistantImages:
            captured_contexts.append(context)
            return AssistantImages(
                api=model.api,
                provider_id=model.provider_id,
                model=model.id,
                output=[ImageContent(source=_FAKE_PNG_B64, media_type="image/png")],
                stop_reason="stop",
            )

    sandbox = FakeSandbox()

    async def fake_register(**kwargs: Any) -> SimpleNamespace:
        return _make_artifact()

    monkeypatch.setattr(
        "cubeplex.tools.builtin.generate_image.register_artifact_from_sandbox",
        fake_register,
    )
    monkeypatch.setattr(
        "cubeplex.tools.builtin.generate_image.resize_to_long_edge",
        lambda data, *, target, jpeg_quality: b"SMALL",
    )

    tool = make_generate_image_tool(
        org_id="org-1",
        workspace_id="ws-1",
        conversation_id="conv-1",
        sandbox=sandbox,  # type: ignore[arg-type]
        images_provider=_SpyProvider2(),
        images_model=_FAKE_MODEL,
    )

    args = GenerateImageInput(prompt="simple")
    result = await tool.execute("tc-6", args, signal=None, on_update=None)

    assert not result.is_error
    assert len(captured_contexts) == 1
    ctx = captured_contexts[0]
    assert ctx.size is None
    assert ctx.quality is None
