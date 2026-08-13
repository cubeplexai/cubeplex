"""E2E coverage for the durable-HITL pause/resume feature.

Covers the routes + checkpointer + Redis state-machine wiring without
relying on a real LLM. The actual agent.respond() execution path needs
a live model and is intentionally stubbed via monkeypatch on
``run_manager._execute_respond_run`` / ``cancel_paused_run``; what we
verify here is that the API surface around them — bootstrap pending_hitl,
the answer routes, claim_resume single-flight, and the steer/cancel
paused-aware branches — does the right thing.

Scenarios:

1. Bootstrap returns ``pending_hitl`` for ask_user + sandbox_confirm.
2. Long-pause recovery: bootstrap still works after Redis active-run and
   run_meta keys age out (load_pending_run_id fallback).
3. POST ask-user-answer claims the slot and spawns the respond task.
4. POST ask-user-answer with mismatched question_id → 409 stale_answer.
5. POST ask-user-answer with no pending → 404 no_pending.
6. Two concurrent POSTs → exactly one 2xx + one 409 ``resume_in_flight``.
7. Steer route on a paused conversation → durable queued row.
8. Cancel route on a paused conversation → dispatches to
   ``cancel_paused_run``.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from cubepi.hitl.types import (
    ApproveRequest,
    AskRequest,
    HitlRequest,
    Option,
    Question,
)

from cubeplex.agents.checkpointer import init_checkpointer
from cubeplex.models.conversation import Conversation
from cubeplex.repositories.conversation_participant import ConversationParticipantRepository
from cubeplex.streams.run_events import _active_run_key, _run_meta_key, create_run


def _ask_pending(question_id: str = "q-ask") -> HitlRequest:
    return HitlRequest(
        question_id=question_id,
        thread_id=None,
        payload=AskRequest(
            questions=[
                Question(
                    key="confirm",
                    prompt="Continue?",
                    options=[
                        Option(label="Yes", value="yes"),
                        Option(label="No", value="no"),
                    ],
                    multi_select=False,
                    required=True,
                )
            ],
        ),
        created_at=time.time(),
        timeout_seconds=None,
    )


def _approve_pending(
    question_id: str = "q-approve",
    tool_call_id: str = "tc-1",
) -> HitlRequest:
    return HitlRequest(
        question_id=question_id,
        thread_id=None,
        payload=ApproveRequest(
            tool_name="execute",
            tool_call_id=tool_call_id,
            args={"command": "rm -rf /tmp/x"},
            details={"matched_pattern": "rm *"},
        ),
        created_at=time.time(),
        timeout_seconds=None,
    )


async def _ensure_creator_participant(conversation_id: str) -> None:
    """Seed the participant row that a real first send would append."""
    from cubeplex.db import async_session_maker

    async with async_session_maker() as session:
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        repo = ConversationParticipantRepository(
            session,
            org_id=conversation.org_id,
            workspace_id=conversation.workspace_id,
        )
        await repo.ensure_participant(conversation_id, conversation.creator_user_id)
        await session.commit()


async def _seed_paused_conversation(
    client: httpx.AsyncClient,
    ws_id: str,
    pending: HitlRequest,
    *,
    title: str = "hitl-paused",
) -> tuple[str, str]:
    """Create a conversation, seed DB pending + Redis paused_hitl row.

    Returns (conversation_id, run_id).
    """
    resp = await client.post(
        f"/api/v1/ws/{ws_id}/conversations",
        params={"title": title},
    )
    assert resp.status_code == 201, resp.text
    conv_id: str = resp.json()["id"]
    await _ensure_creator_participant(conv_id)
    run_id = f"r-{conv_id[:8]}"

    # Seed cubepi_threads.pending_request + run_id atomically (v3 contract).
    async with init_checkpointer() as cp:
        await cp.save_pending_request(conv_id, pending, run_id=run_id)

    # Seed Redis active-run row with status=paused_hitl (mirrors what the
    # terminal block of _run_cubepi_path would write after auto-detach).
    app = client._transport.app  # type: ignore[attr-defined]
    redis = app.state.redis
    prefix = app.state.redis_key_prefix
    await create_run(
        redis,
        prefix=prefix,
        run_id=run_id,
        conversation_id=conv_id,
        status="paused_hitl",
        started_at="2026-06-02T00:00:00+00:00",
        user_message="hi",
        ttl_seconds=3600,
    )
    return conv_id, run_id


@pytest_asyncio.fixture
async def stub_respond_task(
    member_client: tuple[httpx.AsyncClient, str],
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncMock:
    """Stub run_manager._execute_respond_run so spawn-time logic still
    runs (claim_resume, task scheduling) but the spawned task itself
    no-ops (no real agent.respond / LLM call)."""
    client, _ = member_client
    rm = client._transport.app.state.run_manager  # type: ignore[attr-defined]
    mock = AsyncMock()
    monkeypatch.setattr(rm, "_execute_respond_run", mock)
    return mock


@pytest_asyncio.fixture
async def stub_cancel_paused(
    member_client: tuple[httpx.AsyncClient, str],
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncMock:
    """Stub run_manager.cancel_paused_run so the route call path is
    exercised but the transient-agent build (which needs a real LLM
    factory + middleware stack) doesn't run."""
    client, _ = member_client
    rm = client._transport.app.state.run_manager  # type: ignore[attr-defined]
    mock = AsyncMock(return_value="r-cancelled")
    monkeypatch.setattr(rm, "cancel_paused_run", mock)
    return mock


@pytest.mark.asyncio
async def test_bootstrap_returns_pending_hitl_for_ask_user(
    member_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, ws_id = member_client
    conv_id, run_id = await _seed_paused_conversation(client, ws_id, _ask_pending("q-ask-1"))

    resp = await client.get(f"/api/v1/ws/{ws_id}/conversations/{conv_id}/bootstrap")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    pending_hitl = body.get("pending_hitl")
    assert pending_hitl is not None, body
    assert pending_hitl["kind"] == "ask_user"
    assert pending_hitl["question_id"] == "q-ask-1"
    assert pending_hitl["run_id"] == run_id
    assert pending_hitl["requested_at"].endswith("+00:00")
    assert len(pending_hitl["questions"]) == 1
    assert pending_hitl["questions"][0]["key"] == "confirm"


@pytest.mark.asyncio
async def test_bootstrap_returns_pending_hitl_for_sandbox_confirm(
    member_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, ws_id = member_client
    conv_id, run_id = await _seed_paused_conversation(
        client, ws_id, _approve_pending("q-approve-1", "tc-99")
    )

    resp = await client.get(f"/api/v1/ws/{ws_id}/conversations/{conv_id}/bootstrap")
    assert resp.status_code == 200, resp.text
    pending_hitl = resp.json()["pending_hitl"]
    assert pending_hitl["kind"] == "sandbox_confirm"
    assert pending_hitl["tool_call_id"] == "tc-99"
    assert pending_hitl["command"] == "rm -rf /tmp/x"
    assert pending_hitl["matched_pattern"] == "rm *"
    assert pending_hitl["run_id"] == run_id


@pytest.mark.asyncio
async def test_long_pause_recovers_via_db_pending(
    member_client: tuple[httpx.AsyncClient, str],
) -> None:
    """Long pause: Redis active-run + meta age out, but DB pending +
    cubepi v3 run_id column let bootstrap still surface pending_hitl."""
    client, ws_id = member_client
    conv_id, run_id = await _seed_paused_conversation(client, ws_id, _ask_pending("q-long"))

    # Simulate TTL expiry — DEL both Redis keys.
    app = client._transport.app  # type: ignore[attr-defined]
    redis = app.state.redis
    prefix = app.state.redis_key_prefix
    await redis.delete(_active_run_key(prefix, conv_id))
    await redis.delete(_run_meta_key(prefix, run_id))

    resp = await client.get(f"/api/v1/ws/{ws_id}/conversations/{conv_id}/bootstrap")
    assert resp.status_code == 200, resp.text
    pending_hitl = resp.json()["pending_hitl"]
    assert pending_hitl is not None
    # run_id resolved via load_pending_run_id fallback, not Redis.
    assert pending_hitl["run_id"] == run_id


@pytest.mark.asyncio
async def test_bootstrap_pending_hitl_null_for_clean_conversation(
    member_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, ws_id = member_client
    resp = await client.post(
        f"/api/v1/ws/{ws_id}/conversations",
        params={"title": "clean"},
    )
    assert resp.status_code == 201
    conv_id = resp.json()["id"]

    resp = await client.get(f"/api/v1/ws/{ws_id}/conversations/{conv_id}/bootstrap")
    assert resp.status_code == 200
    assert resp.json().get("pending_hitl") is None


@pytest.mark.asyncio
async def test_submit_ask_user_answer_claims_and_spawns_respond(
    member_client: tuple[httpx.AsyncClient, str],
    stub_respond_task: AsyncMock,
) -> None:
    client, ws_id = member_client
    conv_id, run_id = await _seed_paused_conversation(client, ws_id, _ask_pending("q-1"))

    resp = await client.post(
        f"/api/v1/ws/{ws_id}/conversations/{conv_id}/ask-user/q-1",
        json={"answers": {"confirm": "yes"}},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["run_id"] == run_id

    # Redis meta status should now be running (claim_resume CAS flipped it),
    # claim_token set.
    app = client._transport.app  # type: ignore[attr-defined]
    redis = app.state.redis
    prefix = app.state.redis_key_prefix
    meta = await redis.hgetall(_run_meta_key(prefix, run_id))
    # Redis client may have decode_responses=True (strings) or False (bytes);
    # normalize both paths.
    decoded = {
        (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
        for k, v in meta.items()
    }
    assert decoded["status"] == "running"
    assert decoded["claim_token"]

    # Respond task spawned exactly once with the right args.
    await asyncio.sleep(0)  # let the create_task fire
    stub_respond_task.assert_awaited_once()
    kwargs = stub_respond_task.await_args.kwargs
    assert kwargs["run_id"] == run_id
    assert kwargs["conversation_id"] == conv_id
    assert kwargs["question_id"] == "q-1"
    assert kwargs["answer"] == {"confirm": "yes"}
    assert kwargs["claim_token"] == decoded["claim_token"]


@pytest.mark.asyncio
async def test_submit_returns_409_stale_answer_on_qid_mismatch(
    member_client: tuple[httpx.AsyncClient, str],
    stub_respond_task: AsyncMock,
) -> None:
    client, ws_id = member_client
    conv_id, _ = await _seed_paused_conversation(client, ws_id, _ask_pending("q-actual"))

    resp = await client.post(
        f"/api/v1/ws/{ws_id}/conversations/{conv_id}/ask-user/q-wrong",
        json={"answers": {"confirm": "yes"}},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "stale_answer"
    stub_respond_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_submit_returns_404_no_pending(
    member_client: tuple[httpx.AsyncClient, str],
    stub_respond_task: AsyncMock,
) -> None:
    """No pending in DB → 404 no_pending."""
    client, ws_id = member_client
    resp = await client.post(
        f"/api/v1/ws/{ws_id}/conversations",
        params={"title": "no-pending"},
    )
    assert resp.status_code == 201
    conv_id = resp.json()["id"]
    await _ensure_creator_participant(conv_id)

    resp = await client.post(
        f"/api/v1/ws/{ws_id}/conversations/{conv_id}/ask-user/q-none",
        json={"answers": {"confirm": "yes"}},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["code"] == "no_pending"
    stub_respond_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_submit_one_wins(
    member_client: tuple[httpx.AsyncClient, str],
    stub_respond_task: AsyncMock,
) -> None:
    """Two concurrent answer POSTs: exactly one wins the claim_resume
    CAS (202), the other gets 409 resume_in_flight."""
    client, ws_id = member_client
    conv_id, _ = await _seed_paused_conversation(client, ws_id, _ask_pending("q-race"))

    async def submit() -> httpx.Response:
        return await client.post(
            f"/api/v1/ws/{ws_id}/conversations/{conv_id}/ask-user/q-race",
            json={"answers": {"confirm": "yes"}},
        )

    a, b = await asyncio.gather(submit(), submit(), return_exceptions=True)
    # Neither should raise — both return responses; one 202, one 409.
    assert isinstance(a, httpx.Response)
    assert isinstance(b, httpx.Response)
    codes = sorted([a.status_code, b.status_code])
    assert codes == [202, 409], (a.status_code, a.text, b.status_code, b.text)
    loser = a if a.status_code == 409 else b
    assert loser.json()["detail"]["code"] == "resume_in_flight"
    # Respond task spawned exactly once (the winner).
    await asyncio.sleep(0)
    stub_respond_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_steer_route_queues_durably_while_paused_hitl(
    member_client: tuple[httpx.AsyncClient, str],
) -> None:
    """Paused steering is accepted and survives through bootstrap."""
    client, ws_id = member_client
    conv_id, run_id = await _seed_paused_conversation(client, ws_id, _ask_pending("q-steer"))

    resp = await client.post(
        f"/api/v1/ws/{ws_id}/conversations/{conv_id}/steer",
        json={"content": "any steer text", "steer_id": "s-1"},
    )
    assert resp.status_code == 202, resp.text
    assert resp.json() == {"status": "queued", "run_id": run_id, "steer_id": "s-1"}

    bootstrap = await client.get(f"/api/v1/ws/{ws_id}/conversations/{conv_id}/bootstrap")
    assert bootstrap.status_code == 200, bootstrap.text
    assert bootstrap.json()["pending_steers"] == [
        {
            "steer_id": "s-1",
            "content": "any steer text",
            "state": "queued",
            "created_at": bootstrap.json()["pending_steers"][0]["created_at"],
        }
    ]


@pytest.mark.asyncio
async def test_steer_route_queues_for_a_stale_but_resumable_hitl_run(
    member_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, ws_id = member_client
    conv_id, run_id = await _seed_paused_conversation(
        client,
        ws_id,
        _ask_pending("q-steer-stale"),
    )
    app = client._transport.app  # type: ignore[attr-defined]
    await app.state.redis.hset(
        _run_meta_key(app.state.redis_key_prefix, run_id),
        mapping={"status": "stale"},
    )

    response = await client.post(
        f"/api/v1/ws/{ws_id}/conversations/{conv_id}/steer",
        json={"content": "deliver after resume", "steer_id": "s-stale"},
    )

    assert response.status_code == 202, response.text
    assert response.json() == {
        "status": "queued",
        "run_id": run_id,
        "steer_id": "s-stale",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("steer_id", ["", "s" * 65])
async def test_steer_route_rejects_an_invalid_id(
    member_client: tuple[httpx.AsyncClient, str],
    steer_id: str,
) -> None:
    client, ws_id = member_client
    conv_id, _run_id = await _seed_paused_conversation(
        client,
        ws_id,
        _ask_pending("q-steer-id-length"),
    )

    response = await client.post(
        f"/api/v1/ws/{ws_id}/conversations/{conv_id}/steer",
        json={"content": "invalid id", "steer_id": steer_id},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_paused_steer_wakeup_failure_still_returns_accepted(
    member_client: tuple[httpx.AsyncClient, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A post-commit Redis failure must not invite a duplicate client retry."""
    client, ws_id = member_client
    conv_id, run_id = await _seed_paused_conversation(
        client,
        ws_id,
        _ask_pending("q-steer-wakeup"),
    )
    run_manager = client._transport.app.state.run_manager  # type: ignore[attr-defined]
    monkeypatch.setattr(
        run_manager,
        "notify_durable_steer",
        AsyncMock(side_effect=RuntimeError("redis unavailable")),
    )

    response = await client.post(
        f"/api/v1/ws/{ws_id}/conversations/{conv_id}/steer",
        json={"content": "persist this", "steer_id": "s-wakeup"},
    )

    assert response.status_code == 202, response.text
    assert response.json() == {
        "status": "queued",
        "run_id": run_id,
        "steer_id": "s-wakeup",
    }


@pytest.mark.asyncio
async def test_paused_steer_returns_not_found_when_conversation_is_deleted_during_enqueue(
    member_client: tuple[httpx.AsyncClient, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cubeplex.api.routes.v1 import conversations as conversations_route
    from cubeplex.repositories.steering_message import SteeringConversationUnavailableError

    client, ws_id = member_client
    conv_id, _run_id = await _seed_paused_conversation(
        client,
        ws_id,
        _ask_pending("q-steer-delete-race"),
    )

    async def unavailable(*_args: object, **_kwargs: object) -> None:
        raise SteeringConversationUnavailableError(conv_id)

    monkeypatch.setattr(conversations_route.SteeringMessageRepository, "enqueue", unavailable)

    response = await client.post(
        f"/api/v1/ws/{ws_id}/conversations/{conv_id}/steer",
        json={"content": "too late", "steer_id": "s-delete-race"},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_paused_steer_fails_when_run_loses_ownership_after_commit(
    member_client: tuple[httpx.AsyncClient, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run that ends during enqueue cannot leave accepted guidance orphaned."""
    from cubeplex.api.routes.v1 import conversations as conversations_route

    client, ws_id = member_client
    conv_id, run_id = await _seed_paused_conversation(
        client,
        ws_id,
        _ask_pending("q-steer-owner"),
    )
    pending = _ask_pending("q-steer-owner")
    reads = 0

    async def changing_pending(_conversation_id: str) -> tuple[HitlRequest | None, str | None]:
        nonlocal reads
        reads += 1
        return (pending, run_id) if reads == 1 else (None, None)

    async def no_active_run(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(conversations_route, "_load_pending_hitl", changing_pending)
    monkeypatch.setattr(conversations_route, "get_active_run", no_active_run)

    response = await client.post(
        f"/api/v1/ws/{ws_id}/conversations/{conv_id}/steer",
        json={"content": "too late", "steer_id": "s-owner-race"},
    )

    assert response.status_code == 202, response.text
    assert response.json() == {
        "status": "failed",
        "run_id": run_id,
        "steer_id": "s-owner-race",
    }


@pytest.mark.asyncio
async def test_paused_steer_retry_and_cancel_are_durable(
    member_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, ws_id = member_client
    conv_id, run_id = await _seed_paused_conversation(
        client,
        ws_id,
        _ask_pending("q-steer-cancel"),
    )
    path = f"/api/v1/ws/{ws_id}/conversations/{conv_id}/steer"
    body = {"content": "keep this exact text", "steer_id": "s-idempotent"}

    first = await client.post(path, json=body)
    retry = await client.post(path, json=body)
    assert first.status_code == retry.status_code == 202
    assert (
        first.json()
        == retry.json()
        == {
            "status": "queued",
            "run_id": run_id,
            "steer_id": "s-idempotent",
        }
    )

    conflict = await client.post(
        path,
        json={"content": "different text", "steer_id": "s-idempotent"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "steer_id_conflict"

    cancelled = await client.post(
        f"{path}/cancel",
        json={"steer_id": "s-idempotent"},
    )
    assert cancelled.status_code == 202
    assert cancelled.json() == {"status": "cancelled", "run_id": run_id}

    bootstrap = await client.get(f"/api/v1/ws/{ws_id}/conversations/{conv_id}/bootstrap")
    assert bootstrap.status_code == 200
    assert bootstrap.json()["pending_steers"] == []


@pytest.mark.asyncio
async def test_dispatched_cancel_wakeup_failure_still_returns_accepted(
    member_client: tuple[httpx.AsyncClient, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cubeplex.db import async_session_maker
    from cubeplex.repositories import SteeringMessageRepository

    client, ws_id = member_client
    conv_id, run_id = await _seed_paused_conversation(
        client,
        ws_id,
        _ask_pending("q-cancel-wakeup"),
    )
    path = f"/api/v1/ws/{ws_id}/conversations/{conv_id}/steer"
    queued = await client.post(
        path,
        json={"content": "cancel after dispatch", "steer_id": "s-cancel-wakeup"},
    )
    assert queued.status_code == 202, queued.text

    async with async_session_maker() as session:
        conversation = await session.get(Conversation, conv_id)
        assert conversation is not None
        repo = SteeringMessageRepository(
            session,
            org_id=conversation.org_id,
            workspace_id=conversation.workspace_id,
        )
        claimed = await repo.claim_queued(run_id=run_id, owner="remote-owner")
        assert len(claimed) == 1
        await session.commit()

    run_manager = client._transport.app.state.run_manager  # type: ignore[attr-defined]
    monkeypatch.setattr(
        run_manager,
        "notify_durable_cancel",
        AsyncMock(side_effect=RuntimeError("redis unavailable")),
    )
    response = await client.post(
        f"{path}/cancel",
        json={"steer_id": "s-cancel-wakeup"},
    )

    assert response.status_code == 202, response.text
    assert response.json() == {"status": "accepted", "run_id": run_id}


@pytest.mark.asyncio
async def test_cancel_route_paused_dispatches_to_cancel_paused_run(
    member_client: tuple[httpx.AsyncClient, str],
    stub_cancel_paused: AsyncMock,
) -> None:
    """Cancel on a paused_hitl conversation routes to cancel_paused_run,
    not the existing task-cancel path."""
    client, ws_id = member_client
    conv_id, run_id = await _seed_paused_conversation(client, ws_id, _ask_pending("q-cancel"))

    resp = await client.post(f"/api/v1/ws/{ws_id}/conversations/{conv_id}/cancel")
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "cancelled"
    assert body["run_id"] == run_id

    stub_cancel_paused.assert_awaited_once()
    kwargs = stub_cancel_paused.await_args.kwargs
    assert kwargs["conversation_id"] == conv_id
    assert kwargs["run_id"] == run_id
