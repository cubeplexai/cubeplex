"""Phase 2 — Capture langgraph and cubepi outbound HTTP requests for diff analysis.

For each cache-capable provider (deepseek/anthropic, arkcode/openai-completions),
this test invokes the relevant runtime code path directly (no agent loop, no
middleware, no SSE) and captures every outbound HTTP request body to JSON files
in /tmp/cubepi_runtime_capture/<runtime>/<provider>/.

After running, use ``compare_runtimes.py`` to diff the captures:

    uv run python tests/diagnostic/compare_runtimes.py \\
        /tmp/cubepi_runtime_capture/langgraph/deepseek_anthropic \\
        /tmp/cubepi_runtime_capture/cubepi/deepseek_anthropic

    uv run python tests/diagnostic/compare_runtimes.py \\
        /tmp/cubepi_runtime_capture/langgraph/arkcode_openai \\
        /tmp/cubepi_runtime_capture/cubepi/arkcode_openai

Marking: @pytest.mark.real_llm @pytest.mark.diagnostic — opt-in only.
Safe to run in CI without credentials (all tests skip when keys are absent).

## Transport injection strategy

Both langchain_anthropic (langgraph path) and cubepi.AnthropicProvider create
their own httpx.AsyncClient instances internally — either directly or via helper
factories like langchain_anthropic._client_utils._get_default_async_httpx_client.
Patching the SDK __init__ (anthropic.AsyncAnthropic or openai.AsyncOpenAI) is
insufficient because those receive a pre-built http_client and our "if not in
kwargs" guard is skipped.

Instead we patch httpx.AsyncClient.__init__ itself to wrap whatever transport
is being set (or inject one if None) with a CapturingAsyncTransport chain.
This intercepts ALL httpx clients created during the test, regardless of which
layer creates them.

CapturingAsyncTransport wraps the real transport so network I/O still happens.
The label in the filename ("anthropic" or "openai") is inferred from the
destination URL of the first request.

Notes on DeepSeek / cubepi path:
    cubepi.AnthropicProvider (v0.3) does not accept a base_url parameter.
    For the diagnostic we monkey-patch anthropic.AsyncAnthropic.__init__ to
    also inject base_url so requests are directed to api.deepseek.com.
    The langgraph path (ChatAnthropic) already receives base_url from
    LLMFactory.create().
"""

from __future__ import annotations

import pathlib
from typing import Any

import httpx
import pytest

from tests.diagnostic._capture import CapturingAsyncTransport
from tests.diagnostic._common import LONG_SYSTEM_PROMPT, USER_MSG_TURN_1, USER_MSG_TURN_2

pytestmark = [pytest.mark.real_llm, pytest.mark.diagnostic]

CAPTURE_ROOT = pathlib.Path("/tmp/cubepi_runtime_capture")

# ──────────────────────────────────────────────────────────────────────────────
# Shared provider constants
# ──────────────────────────────────────────────────────────────────────────────

DEEPSEEK_BASE_URL = "https://api.deepseek.com/anthropic"
DEEPSEEK_MODEL_ID = "deepseek-v4-pro"
DEEPSEEK_CONTEXT_WINDOW = 65536
DEEPSEEK_MAX_TOKENS = 4096

ARKCODE_BASE_URL = "https://ark.cn-beijing.volces.com/api/coding/v3"
ARKCODE_MODEL_ID = "doubao-seed-2.0-pro"
ARKCODE_CONTEXT_WINDOW = 131072
ARKCODE_MAX_TOKENS = 4096

# ──────────────────────────────────────────────────────────────────────────────
# Transport injection helpers
# ──────────────────────────────────────────────────────────────────────────────


def _install_httpx_client_capture(
    monkeypatch: pytest.MonkeyPatch,
    capture_dir: pathlib.Path,
    label: str,
) -> None:
    """Patch httpx.AsyncHTTPTransport.handle_async_request to capture all outbound requests.

    httpx routes requests through mounts-based dispatch (not just _transport), so
    patching at the AsyncClient or transport-init level misses many requests.  The
    most reliable interception point is the ``handle_async_request`` method of
    ``httpx.AsyncHTTPTransport`` — the concrete transport that actually performs
    TCP connections for https:// URLs.  Every SDK (anthropic, openai, langchain*)
    ultimately calls through this path.

    We capture the request body to JSON before delegating to the real implementation.
    This is a global class-level patch, so it intercepts ALL AsyncHTTPTransport
    instances active during the test, including those built before the patch was
    installed (they call the method through normal Python dispatch).
    """
    capturer = CapturingAsyncTransport(capture_dir=capture_dir, label=label)
    original_handle = httpx.AsyncHTTPTransport.handle_async_request

    async def patched_handle(self: Any, request: httpx.Request) -> httpx.Response:
        # Write capture JSON (reuse CapturingAsyncTransport's internals)
        import json as _json
        from datetime import UTC
        from datetime import datetime as _dt

        capturer._counter += 1
        body_bytes = request.content or b""
        try:
            body: Any = _json.loads(body_bytes.decode("utf-8")) if body_bytes else None
        except Exception:
            body = {"_raw_hex": body_bytes.hex()}

        record: dict[str, Any] = {
            "label": label,
            "counter": capturer._counter,
            "timestamp": _dt.now(UTC).isoformat(),
            "method": request.method,
            "url": str(request.url),
            "headers": {
                k: ("[REDACTED]" if k.lower() in capturer.REDACT_HEADERS else v)
                for k, v in request.headers.items()
            },
            "body": body,
        }
        fname = f"{label}_{capturer._counter:03d}.json"
        (capture_dir / fname).write_text(
            _json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False)
        )
        return await original_handle(self, request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", patched_handle)


# ──────────────────────────────────────────────────────────────────────────────
# Provider / LLM config factories
# ──────────────────────────────────────────────────────────────────────────────


def _make_deepseek_provider_config(api_key: str) -> Any:
    from cubebox.llm.config import ModelConfig, ModelCost, ProviderConfig

    return ProviderConfig(
        base_url=DEEPSEEK_BASE_URL,
        api_key=api_key,
        api="anthropic",
        extra_body={},
        extra_headers={},
        models=[
            ModelConfig(
                id=DEEPSEEK_MODEL_ID,
                name="DeepSeek V4 Pro",
                reasoning=False,
                contextWindow=DEEPSEEK_CONTEXT_WINDOW,
                maxTokens=DEEPSEEK_MAX_TOKENS,
                cost=ModelCost(input=0, output=0),
            )
        ],
    )


def _make_deepseek_llm_config(api_key: str) -> Any:
    from cubebox.llm.config import LLMConfig

    return LLMConfig(
        default_model=f"deepseek/{DEEPSEEK_MODEL_ID}",
        providers={"deepseek": _make_deepseek_provider_config(api_key)},
    )


def _make_arkcode_provider_config(api_key: str) -> Any:
    from cubebox.llm.config import ModelConfig, ModelCost, ProviderConfig

    return ProviderConfig(
        base_url=ARKCODE_BASE_URL,
        api_key=api_key,
        api="openai-completions",
        extra_body={},
        extra_headers={},
        models=[
            ModelConfig(
                id=ARKCODE_MODEL_ID,
                name="Doubao Seed 2.0 Pro",
                reasoning=False,
                contextWindow=ARKCODE_CONTEXT_WINDOW,
                maxTokens=ARKCODE_MAX_TOKENS,
                cost=ModelCost(input=0, output=0),
            )
        ],
    )


def _make_arkcode_llm_config(api_key: str) -> Any:
    from cubebox.llm.config import LLMConfig

    return LLMConfig(
        default_model=f"arkcode/{ARKCODE_MODEL_ID}",
        providers={"arkcode": _make_arkcode_provider_config(api_key)},
    )


# ──────────────────────────────────────────────────────────────────────────────
# LangGraph runtime invocations
# ──────────────────────────────────────────────────────────────────────────────


def _clear_langchain_anthropic_client_cache() -> None:
    """Clear langchain_anthropic's lru_cache so a fresh httpx client is built.

    langchain_anthropic._client_utils caches async httpx clients by base_url
    to avoid rebuilding them on every ChatAnthropic access.  We must clear
    this cache before the test so the patched httpx.AsyncClient.__init__ gets
    called to inject CapturingAsyncTransport.
    """
    try:
        from langchain_anthropic._client_utils import (
            _get_default_async_httpx_client,
            _get_default_httpx_client,
        )

        _get_default_async_httpx_client.cache_clear()
        _get_default_httpx_client.cache_clear()
    except Exception:
        pass  # If import fails, nothing to clear.


async def _run_langgraph_anthropic(api_key: str) -> None:
    """Send two turns via langchain_anthropic.ChatAnthropic (the langgraph path)."""
    from langchain_core.messages import HumanMessage, SystemMessage

    from cubebox.llm.factory import LLMFactory

    llm_config = _make_deepseek_llm_config(api_key)
    factory = LLMFactory(llm_config=llm_config)
    llm = factory.create(model_id=DEEPSEEK_MODEL_ID, provider_name="deepseek")

    sys_msg = SystemMessage(content=LONG_SYSTEM_PROMPT)
    await llm.ainvoke([sys_msg, HumanMessage(content=USER_MSG_TURN_1)])
    await llm.ainvoke([sys_msg, HumanMessage(content=USER_MSG_TURN_2)])


async def _run_langgraph_openai(api_key: str) -> None:
    """Send two turns via langchain_openai.ChatOpenAICompatible (the langgraph path)."""
    from langchain_core.messages import HumanMessage, SystemMessage

    from cubebox.llm.factory import LLMFactory

    llm_config = _make_arkcode_llm_config(api_key)
    factory = LLMFactory(llm_config=llm_config)
    llm = factory.create(model_id=ARKCODE_MODEL_ID, provider_name="arkcode")

    sys_msg = SystemMessage(content=LONG_SYSTEM_PROMPT)
    await llm.ainvoke([sys_msg, HumanMessage(content=USER_MSG_TURN_1)])
    await llm.ainvoke([sys_msg, HumanMessage(content=USER_MSG_TURN_2)])


# ──────────────────────────────────────────────────────────────────────────────
# cubepi runtime invocations
# ──────────────────────────────────────────────────────────────────────────────


async def _run_cubepi_anthropic(api_key: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Send two turns via cubepi.AnthropicProvider (the cubepi path).

    cubepi.AnthropicProvider (v0.3) does not accept base_url.  We inject it
    via a secondary patch on anthropic.AsyncAnthropic.__init__ so the client
    is constructed with base_url=DEEPSEEK_BASE_URL.
    """
    import anthropic
    from cubepi.providers.base import Model as CubepiModel
    from cubepi.providers.base import TextContent, UserMessage

    from cubebox.llm.factory import LLMFactory

    # Secondary patch: inject base_url into every AsyncAnthropic constructed during
    # this call.  The httpx.AsyncClient patch above handles transport capture.
    original_anthropic_init = anthropic.AsyncAnthropic.__init__

    def _patched_anthropic_init(self: Any, *args: Any, **kwargs: Any) -> None:
        if "base_url" not in kwargs:
            kwargs["base_url"] = DEEPSEEK_BASE_URL
        original_anthropic_init(self, *args, **kwargs)

    monkeypatch.setattr(anthropic.AsyncAnthropic, "__init__", _patched_anthropic_init)

    provider_config = _make_deepseek_provider_config(api_key)
    factory = LLMFactory(llm_config=_make_deepseek_llm_config(api_key))
    provider = factory.build_cubepi_provider(provider_config)

    model = CubepiModel(
        id=DEEPSEEK_MODEL_ID,
        provider="deepseek",
        max_tokens=DEEPSEEK_MAX_TOKENS,
        context_window=DEEPSEEK_CONTEXT_WINDOW,
    )

    # Turn 1
    stream1 = await provider.stream(
        model,
        [UserMessage(content=[TextContent(text=USER_MSG_TURN_1)])],
        system_prompt=LONG_SYSTEM_PROMPT,
    )
    async for _ in stream1:
        pass

    # Turn 2
    stream2 = await provider.stream(
        model,
        [UserMessage(content=[TextContent(text=USER_MSG_TURN_2)])],
        system_prompt=LONG_SYSTEM_PROMPT,
    )
    async for _ in stream2:
        pass


async def _run_cubepi_openai(api_key: str) -> None:
    """Send two turns via cubepi.OpenAIProvider (the cubepi path)."""
    from cubepi.providers.base import Model as CubepiModel
    from cubepi.providers.base import TextContent, UserMessage

    from cubebox.llm.factory import LLMFactory

    provider_config = _make_arkcode_provider_config(api_key)
    factory = LLMFactory(llm_config=_make_arkcode_llm_config(api_key))
    provider = factory.build_cubepi_provider(provider_config)

    model = CubepiModel(
        id=ARKCODE_MODEL_ID,
        provider="arkcode",
        max_tokens=ARKCODE_MAX_TOKENS,
        context_window=ARKCODE_CONTEXT_WINDOW,
    )

    # Turn 1
    stream1 = await provider.stream(
        model,
        [UserMessage(content=[TextContent(text=USER_MSG_TURN_1)])],
        system_prompt=LONG_SYSTEM_PROMPT,
    )
    async for _ in stream1:
        pass

    # Turn 2
    stream2 = await provider.stream(
        model,
        [UserMessage(content=[TextContent(text=USER_MSG_TURN_2)])],
        system_prompt=LONG_SYSTEM_PROMPT,
    )
    async for _ in stream2:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# Phase 2 capture tests
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_capture_langgraph_deepseek_anthropic(
    deepseek_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Capture langgraph outbound HTTP for deepseek/anthropic (2 turns)."""
    capture_dir = CAPTURE_ROOT / "langgraph" / "deepseek_anthropic"
    # Clear lru_cache first so the patched httpx.AsyncClient.__init__ is called.
    _clear_langchain_anthropic_client_cache()
    _install_httpx_client_capture(monkeypatch, capture_dir, label="anthropic")

    await _run_langgraph_anthropic(deepseek_api_key)

    files = sorted(capture_dir.glob("*.json"))
    print(f"\n[capture] langgraph/deepseek_anthropic: {len(files)} files captured")
    for f in files:
        print(f"  {f.name}  ({f.stat().st_size} bytes)")
    assert len(files) >= 2, (
        f"Expected at least 2 captured requests (turn 1 + turn 2), got {len(files)}. "
        f"Transport injection may not have worked."
    )


@pytest.mark.asyncio
async def test_capture_cubepi_deepseek_anthropic(
    deepseek_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Capture cubepi outbound HTTP for deepseek/anthropic (2 turns).

    Injects base_url=DEEPSEEK_BASE_URL into AsyncAnthropic.__init__ to work
    around cubepi.AnthropicProvider not accepting base_url natively.
    """
    capture_dir = CAPTURE_ROOT / "cubepi" / "deepseek_anthropic"
    # Clear lru_cache first so the patched httpx.AsyncClient.__init__ is called.
    _clear_langchain_anthropic_client_cache()
    _install_httpx_client_capture(monkeypatch, capture_dir, label="anthropic")

    await _run_cubepi_anthropic(deepseek_api_key, monkeypatch)

    files = sorted(capture_dir.glob("*.json"))
    print(f"\n[capture] cubepi/deepseek_anthropic: {len(files)} files captured")
    for f in files:
        print(f"  {f.name}  ({f.stat().st_size} bytes)")
    assert len(files) >= 2, (
        f"Expected at least 2 captured requests (turn 1 + turn 2), got {len(files)}. "
        f"Transport injection may not have worked."
    )


@pytest.mark.asyncio
async def test_capture_langgraph_arkcode_openai(
    arkcode_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Capture langgraph outbound HTTP for arkcode/openai-completions (2 turns)."""
    capture_dir = CAPTURE_ROOT / "langgraph" / "arkcode_openai"
    _install_httpx_client_capture(monkeypatch, capture_dir, label="openai")

    await _run_langgraph_openai(arkcode_api_key)

    files = sorted(capture_dir.glob("*.json"))
    print(f"\n[capture] langgraph/arkcode_openai: {len(files)} files captured")
    for f in files:
        print(f"  {f.name}  ({f.stat().st_size} bytes)")
    assert len(files) >= 2, (
        f"Expected at least 2 captured requests (turn 1 + turn 2), got {len(files)}. "
        f"Transport injection may not have worked."
    )


@pytest.mark.asyncio
async def test_capture_cubepi_arkcode_openai(
    arkcode_api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Capture cubepi outbound HTTP for arkcode/openai-completions (2 turns)."""
    capture_dir = CAPTURE_ROOT / "cubepi" / "arkcode_openai"
    _install_httpx_client_capture(monkeypatch, capture_dir, label="openai")

    await _run_cubepi_openai(arkcode_api_key)

    files = sorted(capture_dir.glob("*.json"))
    print(f"\n[capture] cubepi/arkcode_openai: {len(files)} files captured")
    for f in files:
        print(f"  {f.name}  ({f.stat().st_size} bytes)")
    assert len(files) >= 2, (
        f"Expected at least 2 captured requests (turn 1 + turn 2), got {len(files)}. "
        f"Transport injection may not have worked."
    )
