"""Lexical-only graceful degradation: API boots and search works with no key.

When `DASHSCOPE_API_KEY` is unset, `start_search_subsystem` keeps
`app.state.embedding_provider = None` but still starts the worker (which
chunks rows with embedding=NULL) and builds the lexical backend. The
search route returns 200 with lexical-only hits — no 503.

This contract test seeds a conversation, drives the worker, hits the
route, and asserts the degraded experience is usable.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text


@pytest.mark.asyncio
async def test_app_boots_without_embedding_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("CUBEPLEX_TEST_LOCAL_EMBED", raising=False)

    # Use the same builder as the rest of the e2e suite so the test runs
    # against the per-slot test DB and a NullPool engine.
    from tests.e2e.conftest import _make_test_app

    app = _make_test_app()
    app.state.deployment_mode = "multi_tenant"

    # TestClient as a context manager runs the FastAPI lifespan; if the
    # embedding path raised, this would fail at __enter__ time.
    with TestClient(app) as client:
        resp = client.get("/health/live")
        assert resp.status_code == 200, resp.text

    assert app.state.embedding_provider is None
    # In lexical-only mode the worker still runs so the lexical leg has
    # something to query.
    assert app.state.embedding_worker is not None
    assert app.state.embedding_worker_task is not None
    assert app.state.lexical_backend is not None


@pytest.mark.asyncio
async def test_search_route_returns_lexical_results_without_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end lexical-only path: seed → worker writes NULL embeddings →
    search route returns lexical hits with vector_count=0."""
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("CUBEPLEX_TEST_LOCAL_EMBED", raising=False)

    from cubepi.providers.base import AssistantMessage, TextContent, UserMessage

    from cubeplex.agents.checkpointer import init_checkpointer
    from cubeplex.db.engine import async_session_maker
    from cubeplex.repositories.embedding_job import EmbeddingJobRepository
    from cubeplex.services.conversation_search.worker import EmbeddingWorker
    from tests.e2e.conftest import (
        DEFAULT_ORG_ID,
        DEFAULT_TEST_EMAIL,
        DEFAULT_TEST_PASSWORD,
        DEFAULT_WS_ID,
        _ensure_default_user_and_membership,
        _make_test_app,
    )
    from tests.e2e.helpers import csrf_cookie_name

    await _ensure_default_user_and_membership()

    # Truncate search tables from any prior test run so _claim_one picks up
    # only the job we enqueue below.
    async with async_session_maker() as _session:
        await _session.execute(text("TRUNCATE TABLE embedding_jobs RESTART IDENTITY"))
        await _session.execute(text("TRUNCATE TABLE conversation_chunks RESTART IDENTITY"))
        await _session.commit()
    app = _make_test_app()
    app.state.deployment_mode = "multi_tenant"

    with TestClient(app) as client:
        # Sanity: provider really is None.
        assert app.state.embedding_provider is None
        # Cancel the lifespan worker so we can drive a deterministic one.
        lifespan_task = getattr(app.state, "embedding_worker_task", None)
        if lifespan_task is not None:
            lifespan_task.cancel()
            try:
                await lifespan_task
            except (asyncio.CancelledError, Exception):
                pass
            app.state.embedding_worker_task = None
            app.state.embedding_worker = None

        # Log in as the default test user.
        client.get("/api/v1/auth/me")  # obtain CSRF cookie
        csrf = client.cookies.get(csrf_cookie_name()) or ""
        login = client.post(
            "/api/v1/auth/login",
            data={"username": DEFAULT_TEST_EMAIL, "password": DEFAULT_TEST_PASSWORD},
            headers={"X-CSRF-Token": csrf},
        )
        assert login.status_code in (200, 204), login.text
        client.headers["X-CSRF-Token"] = client.cookies.get(csrf_cookie_name()) or csrf

        me = client.get("/api/v1/auth/me")
        me.raise_for_status()
        user_id = str(me.json()["id"])

        # Create a conversation and seed messages.
        title_resp = client.post(
            f"/api/v1/ws/{DEFAULT_WS_ID}/conversations",
            params={"title": "lexical docling"},
        )
        title_resp.raise_for_status()
        conv_id = str(title_resp.json()["id"])

        async with init_checkpointer() as cp:
            await cp.append(
                conv_id,
                [
                    UserMessage(content=[TextContent(text="hello docling")], timestamp=1.0),
                    AssistantMessage(content=[TextContent(text="hi there")], timestamp=2.0),
                ],
            )

        async with async_session_maker() as s:
            repo = EmbeddingJobRepository(
                s, org_id=DEFAULT_ORG_ID, workspace_id=DEFAULT_WS_ID, user_id=user_id
            )
            await repo.enqueue(conversation_id=conv_id)

        # Drive worker WITHOUT a provider — exercises the lexical-only path.
        job = await EmbeddingWorker(None)._claim_one()
        # The worker should have processed our job (not a stale one).
        assert job is not None
        assert job.conversation_id == conv_id, (
            f"worker processed wrong conv: {job.conversation_id} != {conv_id}"
        )

        resp = client.get(
            f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/search",
            params={"q": "docling", "limit": 5},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["vector_count"] == 0, f"vector_count={body['vector_count']}"
        assert body["lexical_count"] > 0, (
            f"lexical_count={body['lexical_count']}, results={body['results']}"
        )
        hit_ids = [r["conversation_id"] for r in body["results"]]
        assert conv_id in hit_ids, f"conv_id={conv_id} not in results={hit_ids}"
