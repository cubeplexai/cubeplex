import json

import httpx
import pytest

from cubeplex.services.conversation_search.embedding import EmbeddingProvider


@pytest.mark.asyncio
async def test_embed_returns_vectors() -> None:
    payload = {
        "data": [
            {"embedding": [0.1, 0.2, 0.3], "index": 0},
            {"embedding": [0.4, 0.5, 0.6], "index": 1},
        ]
    }
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=payload))
    provider = EmbeddingProvider(
        base_url="https://example/v1",
        api_key="k",
        model="qwen3-embedding-0.6b",
        vector_dim=3,
        api_dimensions=0,
        timeout_seconds=5,
        _transport=transport,
    )
    vectors = await provider.embed(["hello", "world"])
    assert vectors == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    await provider.aclose()


@pytest.mark.asyncio
async def test_embed_propagates_http_errors() -> None:
    transport = httpx.MockTransport(lambda req: httpx.Response(500, text="boom"))
    provider = EmbeddingProvider(
        base_url="https://example/v1",
        api_key="k",
        model="qwen3-embedding-0.6b",
        vector_dim=3,
        api_dimensions=0,
        timeout_seconds=1,
        _transport=transport,
    )
    with pytest.raises(httpx.HTTPStatusError):
        await provider.embed(["x"])
    await provider.aclose()


def test_model_id_combines_model_and_host() -> None:
    provider = EmbeddingProvider(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key="k",
        model="qwen3-embedding-0.6b",
        vector_dim=1024,
        api_dimensions=0,
        timeout_seconds=5,
    )
    assert provider.model_id == "qwen3-embedding-0.6b@dashscope.aliyuncs.com"


def _capture_transport() -> tuple[httpx.MockTransport, list[dict]]:
    captured: list[dict] = []
    payload = {"data": [{"embedding": [0.0, 0.0, 0.0], "index": 0}]}

    def _handler(req: httpx.Request) -> httpx.Response:
        captured.append(json.loads(req.content.decode()))
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(_handler), captured


@pytest.mark.asyncio
async def test_embed_omits_dimensions_when_zero() -> None:
    transport, captured = _capture_transport()
    provider = EmbeddingProvider(
        base_url="https://example/v1",
        api_key="k",
        model="m",
        vector_dim=3,
        api_dimensions=0,
        timeout_seconds=5,
        _transport=transport,
    )
    await provider.embed(["x"])
    assert "dimensions" not in captured[0]
    await provider.aclose()


@pytest.mark.asyncio
async def test_embed_sends_dimensions_when_set() -> None:
    transport, captured = _capture_transport()
    provider = EmbeddingProvider(
        base_url="https://example/v1",
        api_key="k",
        model="m",
        vector_dim=3,
        api_dimensions=3,
        timeout_seconds=5,
        _transport=transport,
    )
    await provider.embed(["x"])
    assert captured[0]["dimensions"] == 3
    await provider.aclose()
