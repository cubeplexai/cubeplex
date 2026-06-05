"""E2E: generate_image tool — sandbox-gated, artifact registered.

The test drives a conversation through the full cubebox agent stack:
  - FauxProvider returns a scripted generate_image tool_call on turn 1.
  - FauxImagesProvider (from cubepi) returns a 1x1 PNG without hitting OpenAI.
  - LocalSandbox writes the PNG to a temp directory.
  - register_artifact_from_sandbox creates an Artifact row in the test DB.
  - Assertions verify the Artifact row exists with artifact_type == "image"
    and the SSE stream contains a tool_result for generate_image.

Sandbox is activated via a sandbox_factory injected into the test app;
LocalSandbox rewrites absolute /work/ paths to a tmpdir to avoid requiring
a real sandbox service (or root-writable /work).
"""

from __future__ import annotations

import asyncio
import base64
import json
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import cubebox.db as _cubebox_db
from cubebox.api.app import create_app
from cubebox.db.engine import _build_database_url, engine
from cubebox.db.session import get_session
from cubebox.sandbox.base import ExecuteResult, Sandbox
from tests.e2e.conftest import (
    DEFAULT_TEST_EMAIL,
    DEFAULT_TEST_PASSWORD,
    DEFAULT_WS_ID,
    _ensure_default_user_and_membership,
    _lifespan_context,
    _login_and_attach,
)

# ---------------------------------------------------------------------------
# Tiny valid 1x1 PNG — real bytes so Pillow resize_to_long_edge can decode it.
# ---------------------------------------------------------------------------

_PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
_PNG_1x1_B64 = base64.b64encode(_PNG_1x1).decode("ascii")


# ---------------------------------------------------------------------------
# Temp-redirecting sandbox — rewrites absolute /work/... paths to a tmpdir
# so the test doesn't need root-writable /work on the host.
# ---------------------------------------------------------------------------


class _TempLocalSandbox(Sandbox):
    """LocalSandbox variant that redirects /work/* paths to a temp directory."""

    def __init__(self, tmpdir: str) -> None:
        self._tmpdir = tmpdir
        self._id = "temp-local-sandbox"

    def _remap(self, path: str) -> str:
        if path.startswith("/work/"):
            return str(Path(self._tmpdir) / path[len("/work/") :])
        # Fall through — absolute paths outside /work are remapped to tmpdir too
        # (strip leading slash to keep it within tmpdir).
        return str(Path(self._tmpdir) / path.lstrip("/"))

    @property
    def id(self) -> str:
        return self._id

    @property
    def workdir(self) -> str:
        return self._tmpdir

    async def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResult:
        # Remap path arguments in shell commands (base64, test -e, etc.)
        remapped = command
        for keyword in ("/work/",):
            if keyword in remapped:
                # Replace the first occurrence; the path ends at the next space/EOL.
                idx = remapped.index(keyword)
                end_idx = remapped.find(" ", idx)
                original_path = remapped[idx:end_idx] if end_idx != -1 else remapped[idx:]
                remapped_path = self._remap(original_path)
                remapped = remapped.replace(original_path, remapped_path, 1)

        proc = await asyncio.create_subprocess_shell(
            remapped,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=self._tmpdir,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            await proc.communicate()
            return ExecuteResult(output="[timeout]", exit_code=-1)
        return ExecuteResult(output=stdout.decode(errors="replace"), exit_code=proc.returncode)

    async def upload(self, files: list[tuple[str, bytes]]) -> None:
        for path, content in files:
            real_path = Path(self._remap(path))
            real_path.parent.mkdir(parents=True, exist_ok=True)
            real_path.write_bytes(content)

    async def download(self, paths: list[str]) -> list[tuple[str, bytes]]:
        result = []
        for path in paths:
            real = Path(self._remap(path))
            result.append((path, real.read_bytes()))
        return result

    async def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# App + client fixture wired with:
#   - LocalSandbox factory (so generate_image tool is wired)
#   - FauxProvider (scripted tool_call → stop)
#   - FauxImagesProvider (1x1 PNG, no OpenAI)
# ---------------------------------------------------------------------------


def _make_generate_image_test_app(
    sandbox_factory: Any,
) -> Any:
    """Create a test app with NullPool DB + the supplied sandbox_factory."""
    url = _build_database_url()
    test_engine = create_async_engine(url, poolclass=NullPool)
    test_session_maker = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    _cubebox_db.async_session_maker = test_session_maker

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with test_session_maker() as session:
            yield session

    app = create_app(sandbox_factory=sandbox_factory)
    app.dependency_overrides[get_session] = override_get_session
    return app


@pytest_asyncio.fixture
async def generate_image_client(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[httpx.AsyncClient]:
    """Async client with FauxProvider + FauxImagesProvider + LocalSandbox.

    Injection strategy (config-driven path):
    1. Monkeypatch get_image_generation_config() to return enabled=True + a dummy
       api_key so run_manager decides to include the generate_image tool.
    2. Monkeypatch cubepi.providers.images.OpenAIImagesProvider to return a
       FauxImagesProvider instance — no network hit, real tool/sandbox/artifact path.
    """
    await _ensure_default_user_and_membership()

    # --- 1. Enable image_generation config via monkeypatch ---
    from cubepi.providers.images.faux import FauxImagesProvider

    from cubebox.llm.config import ImageGenerationConfig

    _faux_images_instance = FauxImagesProvider(provider_id="faux", png_b64=_PNG_1x1_B64)

    monkeypatch.setattr(
        "cubebox.llm.config.get_image_generation_config",
        lambda: ImageGenerationConfig(
            enabled=True,
            api="openai-images",
            model="gpt-image-2",
            api_key="sk-test-dummy",
        ),
    )

    # --- 2. Monkeypatch OpenAIImagesProvider in run_manager's namespace ---
    # This intercepts the lazy import inside _run_cubepi_path so the faux
    # provider is used without any network call.
    def _fake_openai_images_provider(
        *,
        provider_id: str,
        api_key: str,
        base_url: object = None,
        capability: object = None,
        **kw: object,
    ) -> object:
        return _faux_images_instance

    monkeypatch.setattr(
        "cubepi.providers.images.OpenAIImagesProvider",
        _fake_openai_images_provider,
    )

    # --- 4. Set up FauxProvider scripted responses ---
    from cubepi.providers.faux import (
        FauxProvider,
        faux_assistant_message,
        faux_text,
        faux_tool_call,
    )

    from cubebox.llm.factory import LLMFactory

    faux_provider = FauxProvider()
    faux_provider.set_responses(
        [
            # Turn 1: agent calls generate_image tool.
            faux_assistant_message(
                [faux_tool_call("generate_image", {"prompt": "a cat"})],
                stop_reason="tool_use",
            ),
            # Turn 2: agent produces a text reply after seeing the tool result.
            faux_assistant_message(
                [faux_text("I generated the image.")],
                stop_reason="stop",
            ),
        ]
    )

    # Monkeypatch LLMFactory.build_cubepi_provider to return our FauxProvider.
    monkeypatch.setattr(
        LLMFactory,
        "build_cubepi_provider",
        lambda self, provider_config, **kw: faux_provider,
    )

    # --- 5. Create temp sandbox directory ---
    with tempfile.TemporaryDirectory(prefix="cubebox_e2e_img_") as tmpdir:
        sandbox_factory = lambda: _TempLocalSandbox(tmpdir)  # noqa: E731

        app = _make_generate_image_test_app(sandbox_factory=sandbox_factory)
        app.state.deployment_mode = "multi_tenant"

        async with _lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                await _login_and_attach(c, DEFAULT_TEST_EMAIL, DEFAULT_TEST_PASSWORD)
                yield c

    await engine.dispose()


# ---------------------------------------------------------------------------
# Helper: stream SSE until done/error
# ---------------------------------------------------------------------------


async def _stream_to_done(
    client: httpx.AsyncClient,
    ws_id: str,
    conv_id: str,
    body: dict[str, Any],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    async with client.stream(
        "POST",
        f"/api/v1/ws/{ws_id}/conversations/{conv_id}/messages",
        json=body,
        headers={"accept": "text/event-stream"},
    ) as resp:
        assert resp.status_code == 200, resp.text
        async for line in resp.aiter_lines():
            if not line.startswith("data: "):
                continue
            payload = json.loads(line[len("data: ") :])
            events.append(payload)
            if payload.get("type") in {"done", "error"}:
                return events
    return events


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_image_creates_artifact(
    generate_image_client: httpx.AsyncClient,
) -> None:
    """generate_image tool call → Artifact row with artifact_type='image'."""
    client = generate_image_client
    ws_id = DEFAULT_WS_ID

    # 1. Create a conversation.
    resp = await client.post(
        f"/api/v1/ws/{ws_id}/conversations",
        params={"title": "generate-image-e2e"},
    )
    assert resp.status_code == 201, f"conversation creation failed: {resp.text}"
    conv_id = resp.json()["id"]

    # 2. Drive the agent turn.  FauxProvider returns a generate_image tool_call
    #    on the first request and a text reply on the second.
    events = await _stream_to_done(
        client,
        ws_id,
        conv_id,
        {"content": "Generate a picture of a cat."},
    )

    # 3. No error events.
    errors = [e for e in events if e.get("type") == "error"]
    assert not errors, f"unexpected error events: {errors!r}"

    # 4. Stream terminates with 'done'.
    assert any(e.get("type") == "done" for e in events), (
        f"no 'done' event; seen types: {[e.get('type') for e in events]!r}"
    )

    # 5. A generate_image tool_call was emitted.
    def _tool_name(evt: dict[str, Any]) -> str:
        data = evt.get("data")
        if isinstance(data, dict):
            return str(data.get("name", ""))
        return str(evt.get("name", ""))

    tool_calls = [e for e in events if e.get("type") == "tool_call"]
    gen_calls = [e for e in tool_calls if _tool_name(e) == "generate_image"]
    assert gen_calls, (
        f"no generate_image tool_call in stream.\n"
        f"  tool_call events: {tool_calls!r}\n"
        f"  all types: {[e.get('type') for e in events]!r}"
    )

    # 6. A tool_result was emitted for generate_image.
    tool_results = [e for e in events if e.get("type") == "tool_result"]
    assert tool_results, f"no tool_result events in stream: {[e.get('type') for e in events]!r}"

    # 7. Artifact row exists in the DB with artifact_type == "image".
    test_engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            from sqlalchemy import select

            from cubebox.models import Artifact

            stmt = select(Artifact).where(
                Artifact.conversation_id == conv_id,  # type: ignore[arg-type]
                Artifact.artifact_type == "image",  # type: ignore[arg-type]
            )
            result = (await session.execute(stmt)).scalars().all()
            assert result, (
                f"no Artifact row with artifact_type='image' for conv_id={conv_id!r}. "
                f"Artifact rows for this conv: "
                + repr(
                    (
                        await session.execute(
                            select(Artifact).where(
                                Artifact.conversation_id == conv_id  # type: ignore[arg-type]
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            )
            artifact = result[0]
            assert artifact.artifact_type == "image"
            assert artifact.name.endswith(".png"), (
                f"expected artifact name ending in .png, got: {artifact.name!r}"
            )
    finally:
        await test_engine.dispose()
