"""HTTP Stop persists its explicit target before best-effort delivery."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from cubeloop.providers.faux import FauxProvider, faux_assistant_message, faux_tool_call
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from cubeplex.agents.checkpointer import shared_checkpointer
from cubeplex.db.engine import async_session_maker
from cubeplex.models import Conversation, ConversationExecutionAdmission
from cubeplex.services.conversation_execution import (
    AdmittedExecution,
    ConversationExecutionService,
    UserMessageIntent,
)
from cubeplex.streams.run_events import (
    _active_run_key,
    _run_meta_key,
    create_run,
    get_active_run,
    get_run_meta,
)
from cubeplex.streams.run_manager import RunContext
from tests.e2e.test_admitted_run_execution import cleanup_run_rows
from tests.e2e.test_conversation_execution_control import snapshot


async def admit(
    session: AsyncSession, conversation_id: str, *, source_id: str | None = None
) -> AdmittedExecution:
    conversation = await session.get(Conversation, conversation_id)
    assert conversation is not None
    admitted = await ConversationExecutionService(
        session, org_id=conversation.org_id, workspace_id=conversation.workspace_id
    ).admit_user_message(
        conversation_id=conversation_id,
        actor_user_id=conversation.creator_user_id,
        namespace="web",
        source_id=source_id or str(uuid4()),
        intent=UserMessageIntent(content="do the work"),
        snapshot=snapshot(),
        now=datetime.now(UTC),
    )
    await session.commit()
    return admitted


async def create_conversation(client: httpx.AsyncClient, workspace_id: str) -> str:
    response = await client.post(f"/api/v1/ws/{workspace_id}/conversations")
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


@pytest.mark.parametrize(
    ("action", "body"),
    [
        ("cancel", None),
        ("cancel", {}),
        ("cancel", {"run_id": ""}),
        ("cancel", {"run_id": " ".ljust(65)}),
        ("cancel", {"run_id": "run", "execution_generation": 0}),
        ("stop-all", {}),
        ("stop-all", {"execution_generation": -1}),
        ("stop-all", {"execution_generation": True}),
        ("stop-all", {"execution_generation": "0"}),
    ],
)
async def test_stop_requires_an_explicit_valid_target(
    member_client: tuple[httpx.AsyncClient, str], action: str, body: object
) -> None:
    client, workspace_id = member_client
    conversation_id = await create_conversation(client, workspace_id)
    response = await client.post(
        f"/api/v1/ws/{workspace_id}/conversations/{conversation_id}/{action}", json=body
    )
    assert response.status_code == 422, response.text


async def test_stop_retry_never_selects_the_new_active_run(
    member_client: tuple[httpx.AsyncClient, str], db_session: AsyncSession
) -> None:
    client, workspace_id = member_client
    conversation_id = await create_conversation(client, workspace_id)
    old = await admit(db_session, conversation_id)
    new = await admit(db_session, conversation_id)
    assert new.admission.run_id
    app = client._transport.app  # type: ignore[attr-defined]
    await create_run(
        app.state.redis,
        prefix=app.state.redis_key_prefix,
        conversation_id=conversation_id,
        run_id=new.admission.run_id,
        status="running",
        started_at=datetime.now(UTC).isoformat(),
        ttl_seconds=60,
    )
    path = f"/api/v1/ws/{workspace_id}/conversations/{conversation_id}/cancel"
    for _ in range(2):
        response = await client.post(path, json={"run_id": old.admission.run_id})
        assert response.status_code == 202, response.text
        assert response.json() == {
            "run_id": old.admission.run_id,
            "accepted": True,
            "cleanup_pending": True,
        }
    await db_session.refresh(old.admission)
    await db_session.refresh(new.admission)
    assert old.admission.run_stop_requested_at is not None
    assert new.admission.run_stop_requested_at is None
    conversation = await db_session.get(Conversation, conversation_id)
    assert conversation is not None and conversation.execution_closed_at is None
    active = await get_active_run(
        app.state.redis, prefix=app.state.redis_key_prefix, conversation_id=conversation_id
    )
    assert active is not None and active.run_id == new.admission.run_id


@pytest.mark.parametrize("action", ["cancel", "stop-all"])
async def test_stop_commits_before_signal_and_survives_signal_failure(
    member_client: tuple[httpx.AsyncClient, str],
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    client, workspace_id = member_client
    conversation_id = await create_conversation(client, workspace_id)
    admitted = await admit(db_session, conversation_id)
    app = client._transport.app  # type: ignore[attr-defined]
    observed = []

    async def fail_publish(*args: object, **kwargs: object) -> None:
        async with async_session_maker() as session:
            row = await session.get(ConversationExecutionAdmission, admitted.admission.id)
            assert row is not None
            observed.append(row.run_stop_requested_at if action == "cancel" else row.revoked_at)
        raise ConnectionError("control transport unavailable")

    monkeypatch.setattr(app.state.run_manager._redis, "publish", fail_publish)
    body = (
        {"run_id": admitted.admission.run_id}
        if action == "cancel"
        else {"execution_generation": admitted.admission.execution_generation}
    )
    response = await client.post(
        f"/api/v1/ws/{workspace_id}/conversations/{conversation_id}/{action}", json=body
    )
    assert response.status_code == 202, response.text
    assert response.json() == {**body, "accepted": True, "cleanup_pending": True}
    assert len(observed) == 1 and observed[0] is not None


async def test_stop_all_retry_cannot_close_a_new_generation(
    member_client: tuple[httpx.AsyncClient, str], db_session: AsyncSession
) -> None:
    client, workspace_id = member_client
    conversation_id = await create_conversation(client, workspace_id)
    first = await admit(db_session, conversation_id)
    path = f"/api/v1/ws/{workspace_id}/conversations/{conversation_id}/stop-all"
    body = {"execution_generation": first.admission.execution_generation}
    response = await client.post(path, json=body)
    assert response.status_code == 202, response.text
    assert response.json()["cleanup_pending"] is True
    second = await admit(db_session, conversation_id)
    assert second.admission.execution_generation == first.admission.execution_generation + 1
    response = await client.post(path, json=body)
    assert response.status_code == 202, response.text
    await db_session.refresh(second.admission)
    assert second.admission.revoked_at is None
    conversation = await db_session.get(Conversation, conversation_id)
    assert conversation is not None and conversation.execution_closed_at is None


async def test_stop_rejects_unrelated_run_and_future_generation(
    member_client: tuple[httpx.AsyncClient, str], db_session: AsyncSession
) -> None:
    client, workspace_id = member_client
    conversation_id = await create_conversation(client, workspace_id)
    other_id = await create_conversation(client, workspace_id)
    other = await admit(db_session, other_id)
    path = f"/api/v1/ws/{workspace_id}/conversations/{conversation_id}"
    response = await client.post(f"{path}/cancel", json={"run_id": other.admission.run_id})
    assert response.status_code == 404, response.text
    response = await client.post(f"{path}/stop-all", json={"execution_generation": 1})
    assert response.status_code == 409, response.text
    await db_session.refresh(other.admission)
    assert other.admission.run_stop_requested_at is None and other.admission.revoked_at is None


@pytest.mark.parametrize("action", ["cancel", "stop-all"])
async def test_failed_stop_transaction_does_not_signal_or_revoke(
    member_client: tuple[httpx.AsyncClient, str],
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    client, workspace_id = member_client
    conversation_id = await create_conversation(client, workspace_id)
    admitted = await admit(db_session, conversation_id)
    app = client._transport.app  # type: ignore[attr-defined]
    published = []

    async def capture_publish(*args: object, **kwargs: object) -> int:
        published.append(args)
        return 0

    def mark_stop_write(session: Session, context: object, instances: object) -> None:
        for row in session.dirty:
            if (
                isinstance(row, ConversationExecutionAdmission)
                and row.id == admitted.admission.id
                and (row.run_stop_requested_at is not None or row.revoked_at is not None)
            ):
                session.info["fail_run_stop_commit"] = True

    def fail_commit(session: Session) -> None:
        if session.info.get("fail_run_stop_commit"):
            raise RuntimeError("stop commit unavailable")

    monkeypatch.setattr(app.state.run_manager._redis, "publish", capture_publish)
    body = (
        {"run_id": admitted.admission.run_id}
        if action == "cancel"
        else {"execution_generation": admitted.admission.execution_generation}
    )
    event.listen(Session, "before_flush", mark_stop_write)
    event.listen(Session, "before_commit", fail_commit)
    try:
        with pytest.raises(RuntimeError, match="stop commit unavailable"):
            await client.post(
                f"/api/v1/ws/{workspace_id}/conversations/{conversation_id}/{action}", json=body
            )
    finally:
        event.remove(Session, "before_commit", fail_commit)
        event.remove(Session, "before_flush", mark_stop_write)
    assert published == []
    await db_session.refresh(admitted.admission)
    assert admitted.admission.run_stop_requested_at is None
    assert admitted.admission.revoked_at is None
    conversation = await db_session.get(Conversation, conversation_id, populate_existing=True)
    assert conversation is not None and conversation.execution_closed_at is None


@pytest.mark.parametrize("action", ["cancel", "stop-all"])
async def test_stop_cannot_cross_workspace_scope(
    member_client_two_workspaces: tuple[httpx.AsyncClient, str, str],
    db_session: AsyncSession,
    action: str,
) -> None:
    client, workspace_id, other_workspace_id = member_client_two_workspaces
    conversation_id = await create_conversation(client, workspace_id)
    admitted = await admit(db_session, conversation_id)
    body = (
        {"run_id": admitted.admission.run_id}
        if action == "cancel"
        else {"execution_generation": admitted.admission.execution_generation}
    )
    response = await client.post(
        f"/api/v1/ws/{other_workspace_id}/conversations/{conversation_id}/{action}", json=body
    )
    assert response.status_code == 404, response.text
    await db_session.refresh(admitted.admission)
    assert (
        admitted.admission.run_stop_requested_at is None and admitted.admission.revoked_at is None
    )


@pytest.mark.parametrize("action", ["cancel", "stop-all"])
async def test_stop_paused_run_after_redis_expiry_without_model(
    member_client: tuple[httpx.AsyncClient, str],
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    client, workspace_id = member_client
    conversation_id = await create_conversation(client, workspace_id)
    admitted = await admit(db_session, conversation_id)
    current = snapshot()
    provider_config = current.providers["provider"].model_copy(deep=True)
    for model in provider_config.models:
        model.context_window = 128_000
        model.max_tokens = 4096
    current = replace(current, providers={"provider": provider_config})
    provider = FauxProvider(provider_id="provider")
    provider.set_responses(
        [
            faux_assistant_message(
                faux_tool_call(
                    "ask_user", {"questions": [{"key": "choice", "prompt": "Continue?"}]}
                ),
                stop_reason="tool_use",
            )
        ]
    )
    calls = []
    provider.subscribe_request(lambda payload, model: calls.append(model.id))
    monkeypatch.setattr("cubeplex.llm.builder.build_provider", lambda *args, **kwargs: provider)
    app = client._transport.app  # type: ignore[attr-defined]
    manager = app.state.run_manager
    try:
        run_id = await manager.start_run(
            conversation_id=conversation_id,
            content="do the work",
            ctx=RunContext(
                user_id=admitted.admission.actor_user_id,
                org_id=admitted.admission.org_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                is_group_chat=True,
            ),
            run_id=admitted.admission.run_id,
            admission_id=admitted.admission.id,
            llm_snapshot=current,
        )
        await manager.drain(timeout_seconds=15)
        async with shared_checkpointer() as cp:
            assert await cp.load_pending(conversation_id) is not None
        await app.state.redis.delete(
            _active_run_key(app.state.redis_key_prefix, conversation_id),
            _run_meta_key(app.state.redis_key_prefix, run_id),
        )
        body = (
            {"run_id": run_id}
            if action == "cancel"
            else {"execution_generation": admitted.admission.execution_generation}
        )
        response = await client.post(
            f"/api/v1/ws/{workspace_id}/conversations/{conversation_id}/{action}", json=body
        )
        assert response.status_code == 202, response.text
        assert response.json()["accepted"] is True
        await manager.drain(timeout_seconds=15)
        assert calls == ["first"]
        async with shared_checkpointer() as cp:
            assert await cp.load_pending(conversation_id) is None
        meta = await get_run_meta(app.state.redis, prefix=app.state.redis_key_prefix, run_id=run_id)
        assert meta is not None and meta.status == "cancelled"
        await db_session.refresh(admitted.admission)
        assert admitted.admission.run_finished_at is not None
        retry = await client.post(
            f"/api/v1/ws/{workspace_id}/conversations/{conversation_id}/{action}", json=body
        )
        assert retry.status_code == 202 and retry.json()["cleanup_pending"] is False
    finally:
        await manager.cancel_all()
        await cleanup_run_rows(db_session, conversation_id)
