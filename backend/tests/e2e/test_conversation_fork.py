"""E2E: fork conversation endpoint.

Covers the four contract guarantees the route promises (happy path,
group-chat reject, cross-workspace 404, bogus run → 400). We seed
cubepi state directly with ``claim_run`` + ``append`` + ``mark_run_complete``
so the test does not need to call a real LLM — fork semantics are about
state mechanics, not model behavior.

Each test cleans up its source + fork rows AND the corresponding cubepi
threads + messages + runs. The default workspace is shared across the
suite; orphan rows accumulating here would flake any later test that
list/counts conversations under it.
"""

from __future__ import annotations

import httpx
import pytest
from cubepi.providers.base import AssistantMessage, TextContent, UserMessage
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubebox.agents.checkpointer import init_checkpointer
from cubebox.db.engine import _build_database_url
from cubebox.models import Conversation
from tests.e2e.conftest import DEFAULT_WS_ID

pytestmark = pytest.mark.e2e


async def _seed_completed_run(
    conversation_id: str,
    *,
    run_id: str,
    user_text: str,
    assistant_text: str,
) -> None:
    """Append a user/assistant turn under ``run_id`` and mark the run complete."""
    async with init_checkpointer() as cp:
        await cp.claim_run(conversation_id, run_id)
        await cp.append(
            conversation_id,
            [
                UserMessage(
                    content=[TextContent(text=user_text)],
                    timestamp=1.0,
                    run_id=run_id,
                ),
                AssistantMessage(
                    content=[TextContent(text=assistant_text)],
                    timestamp=2.0,
                    run_id=run_id,
                ),
            ],
        )
        await cp.mark_run_complete(conversation_id, run_id)


async def _set_group_chat(conversation_id: str) -> None:
    """Flip is_group_chat on a conversation row (bypass higher-level checks)."""
    engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            await session.execute(
                update(Conversation)
                .where(Conversation.id == conversation_id)  # type: ignore[arg-type]
                .values(is_group_chat=True)
            )
            await session.commit()
    finally:
        await engine.dispose()


async def _cleanup(conversation_ids: list[str]) -> None:
    """Hard-delete the conv rows AND their cubepi state.

    The route soft-deletes (sets ``deleted_at``); we want hard deletes so
    counts/aggregates over the shared default workspace stay clean across
    runs.

    Iterate in REVERSE insertion order — tests append src then new_id, and
    ``cubepi_threads.parent_thread_id`` is a self-FK from the fork back to
    its source. Deleting the source first would orphan the FK; reversing
    drops the child first.
    """
    if not conversation_ids:
        return
    engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            for cid in reversed(conversation_ids):
                # cubepi state — ``cubepi_runs`` has FK into ``cubepi_threads``,
                # so messages + runs go before the thread row.
                await session.execute(
                    text("DELETE FROM cubepi_messages WHERE thread_id = :t"),
                    {"t": cid},
                )
                await session.execute(
                    text("DELETE FROM cubepi_runs WHERE thread_id = :t"),
                    {"t": cid},
                )
                await session.execute(
                    text("DELETE FROM cubepi_threads WHERE thread_id = :t"),
                    {"t": cid},
                )
                # Dependent cubebox tables that the fork route creates rows in
                # (search index enqueue → conversation_chunks + embedding_jobs).
                # Drop these before the conversation row so its FK targets are
                # free.
                await session.execute(
                    text("DELETE FROM conversation_chunks WHERE conversation_id = :id"),
                    {"id": cid},
                )
                await session.execute(
                    text("DELETE FROM embedding_jobs WHERE conversation_id = :id"),
                    {"id": cid},
                )
                # cubebox conversation row.
                await session.execute(
                    text("DELETE FROM conversations WHERE id = :id"),
                    {"id": cid},
                )
            await session.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_fork_happy_path(memory_client: httpx.AsyncClient) -> None:
    created: list[str] = []
    try:
        resp = await memory_client.post(
            f"/api/v1/ws/{DEFAULT_WS_ID}/conversations", params={"title": "src for fork"}
        )
        assert resp.status_code == 201, resp.text
        src_id = resp.json()["id"]
        created.append(src_id)

        await _seed_completed_run(
            src_id, run_id="run-fork-1", user_text="ping", assistant_text="pong"
        )

        resp = await memory_client.post(
            f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{src_id}/fork",
            json={"after_run_id": "run-fork-1"},
        )
        assert resp.status_code == 201, resp.text
        new_conv = resp.json()
        new_id = new_conv["id"]
        created.append(new_id)
        assert new_id != src_id
        assert new_conv["title"].endswith(" — fork")
        assert new_conv["is_pinned"] is False
        assert new_conv["is_group_chat"] is False

        # Forked thread carries the same message bodies (by role+text) as the src.
        src_msgs = (
            await memory_client.get(f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{src_id}/messages")
        ).json()["messages"]
        new_msgs = (
            await memory_client.get(f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{new_id}/messages")
        ).json()["messages"]

        def _shape(msgs: list[dict[str, object]]) -> list[tuple[str, str]]:
            out: list[tuple[str, str]] = []
            for m in msgs:
                content = m.get("content")
                msg_text = ""
                if isinstance(content, list) and content:
                    first = content[0]
                    if isinstance(first, dict):
                        msg_text = str(first.get("text", ""))
                out.append((str(m.get("role")), msg_text))
            return out

        assert _shape(new_msgs) == _shape(src_msgs)
    finally:
        await _cleanup(created)


@pytest.mark.asyncio
async def test_fork_run_not_completed_returns_400(memory_client: httpx.AsyncClient) -> None:
    created: list[str] = []
    try:
        resp = await memory_client.post(
            f"/api/v1/ws/{DEFAULT_WS_ID}/conversations", params={"title": "src bad run"}
        )
        src_id = resp.json()["id"]
        created.append(src_id)
        await _seed_completed_run(src_id, run_id="run-good", user_text="hi", assistant_text="hey")

        resp = await memory_client.post(
            f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{src_id}/fork",
            json={"after_run_id": "run-does-not-exist"},
        )
        # cubepi raises RunNotCompletedError for both "no row" and "row not done".
        assert resp.status_code == 400, resp.text
        assert resp.json()["detail"]["code"] == "run_not_completed"
    finally:
        await _cleanup(created)


@pytest.mark.asyncio
async def test_fork_group_chat_rejected(memory_client: httpx.AsyncClient) -> None:
    created: list[str] = []
    try:
        resp = await memory_client.post(
            f"/api/v1/ws/{DEFAULT_WS_ID}/conversations", params={"title": "src group"}
        )
        src_id = resp.json()["id"]
        created.append(src_id)
        await _seed_completed_run(src_id, run_id="run-group", user_text="hi", assistant_text="hey")
        await _set_group_chat(src_id)

        resp = await memory_client.post(
            f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{src_id}/fork",
            json={"after_run_id": "run-group"},
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["detail"]["code"] == "group_chat_not_supported"
    finally:
        await _cleanup(created)


@pytest.mark.asyncio
async def test_fork_cross_workspace_returns_404(
    memory_client: httpx.AsyncClient,
    member_client: tuple[httpx.AsyncClient, str],
) -> None:
    """A user in WS A cannot fork a conversation that lives in WS B."""
    other_client, other_ws = member_client
    created: list[str] = []
    try:
        resp = await memory_client.post(
            f"/api/v1/ws/{DEFAULT_WS_ID}/conversations", params={"title": "src xws"}
        )
        src_id = resp.json()["id"]
        created.append(src_id)
        await _seed_completed_run(src_id, run_id="run-xws", user_text="hi", assistant_text="hey")

        # Path-scope a request to the *other* workspace pointing at this src id.
        # 404 — visibility is enforced by ConversationRepository, not 403.
        resp = await other_client.post(
            f"/api/v1/ws/{other_ws}/conversations/{src_id}/fork",
            json={"after_run_id": "run-xws"},
        )
        assert resp.status_code == 404, resp.text

        # Also confirm: that other user, in their own workspace, sees 404 even
        # if they happen to use the matching workspace path for the src row.
        resp2 = await other_client.post(
            f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{src_id}/fork",
            json={"after_run_id": "run-xws"},
        )
        # require_member rejects access to a workspace they aren't a member of
        # before the route logic runs. 403 or 404 both prove isolation; we
        # accept either because both shapes mean "you can't see it."
        assert resp2.status_code in (403, 404), resp2.text
    finally:
        await _cleanup(created)
