"""End-to-end: seed conversations → enqueue → drive worker → call search API.

Requires a real embedding endpoint (OpenAI-compatible). Skipped cleanly
when neither DASHSCOPE_API_KEY nor CUBEBOX_TEST_LOCAL_EMBED is set so the
default test pass stays hermetic; CI sets DASHSCOPE_API_KEY to exercise
the real-network path. The test bypasses EmbeddingProvider.from_config so
it doesn't depend on whether the operator wired the key into config —
the env-var skip-gate above is the only knob.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from cubebox.agents.checkpointer import init_checkpointer
from cubebox.db.engine import async_session_maker
from cubebox.repositories.embedding_job import EmbeddingJobRepository
from cubebox.services.conversation_search.embedding import EmbeddingProvider
from cubebox.services.conversation_search.worker import EmbeddingWorker
from tests.e2e.conftest import DEFAULT_ORG_ID, DEFAULT_WS_ID

# Skip the entire module unless a real embedding endpoint is available.
# Use module-level skip rather than per-test so collection costs (importing
# heavy cubepi modules) stay low when the suite runs without the secret.
_EMBED_KEY = os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("CUBEBOX_TEST_LOCAL_EMBED")
pytestmark = pytest.mark.skipif(
    not _EMBED_KEY,
    reason="No embedding endpoint configured; set DASHSCOPE_API_KEY or CUBEBOX_TEST_LOCAL_EMBED.",
)


async def _seed_conv(client: TestClient, title: str, user_text: str) -> str:
    """Create a conversation via API and append cubepi messages directly."""
    from cubepi.providers.base import AssistantMessage, TextContent, UserMessage

    resp = client.post(
        f"/api/v1/ws/{DEFAULT_WS_ID}/conversations",
        params={"title": title},
    )
    resp.raise_for_status()
    conv_id = str(resp.json()["id"])
    async with init_checkpointer() as cp:
        await cp.append(
            conv_id,
            [
                UserMessage(content=[TextContent(text=user_text)], timestamp=1.0),
                AssistantMessage(content=[TextContent(text="ack")], timestamp=2.0),
            ],
        )
    return conv_id


@pytest.mark.asyncio
async def test_e2e_search_finds_seeded_conversations(client: TestClient) -> None:
    # Cancel the lifespan worker so a single provider drains the queue
    # below and the test stays deterministic.
    import asyncio as _aio

    lifespan_task = getattr(client.app.state, "embedding_worker_task", None)
    if lifespan_task is not None:
        lifespan_task.cancel()
        try:
            await lifespan_task
        except (_aio.CancelledError, Exception):
            pass
        client.app.state.embedding_worker_task = None
        client.app.state.embedding_worker = None

    me = client.get("/api/v1/auth/me")
    me.raise_for_status()
    user_id = str(me.json()["id"])

    en_conv = await _seed_conv(
        client, "docling-notes", "docling is a PDF parser for agent pipelines"
    )
    zh_conv = await _seed_conv(client, "解析工具", "docling 是一款用于智能体的文档解析工具")

    async with async_session_maker() as s:
        repo = EmbeddingJobRepository(
            s,
            org_id=DEFAULT_ORG_ID,
            workspace_id=DEFAULT_WS_ID,
            user_id=user_id,
        )
        for conv_id in (en_conv, zh_conv):
            await repo.enqueue(conversation_id=conv_id)

    # Drive the worker until the queue drains. The lifespan-managed worker
    # would also drain it; doing it inline keeps the test deterministic.
    # Build the provider explicitly so the test doesn't depend on whether
    # the operator wired search.embedding.api_key into config.
    provider = EmbeddingProvider(
        base_url=os.environ.get(
            "CUBEBOX_TEST_EMBED_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
        api_key=str(_EMBED_KEY),
        model=os.environ.get("CUBEBOX_TEST_EMBED_MODEL", "text-embedding-v4"),
        vector_dim=1024,
        api_dimensions=1024,
        timeout_seconds=30,
    )
    try:
        worker = EmbeddingWorker(provider)
        while await worker._claim_one() is not None:
            pass
    finally:
        await provider.aclose()

    # English keyword
    resp = client.get(
        f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/search",
        params={"q": "docling"},
    )
    assert resp.status_code == 200, resp.text
    en_results = resp.json()["results"]
    assert any(r["conversation_id"] == en_conv for r in en_results)

    # Chinese keyword
    resp = client.get(
        f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/search",
        params={"q": "文档解析"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["fused_count"] > 0
    assert any(r["conversation_id"] == zh_conv for r in body["results"])
