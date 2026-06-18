"""E2E tests for group chat: messaging, sandbox isolation, HITL, sender attribution.

Companion to ``test_topics.py`` which covers Topic CRUD and participant
management. This module exercises the run-time behaviour that depends on
``Conversation.topic_id`` + topic participants — message access gates,
sandbox mode dispatch, IM resume refusal, and the cubepi-side sender
attribution helper.

Send-message tests assert ``status_code != 404`` because the
``four_layer_admin_and_member`` fixture does not seed an LLM preset; the
participant-gate runs BEFORE preset resolution, so a passing gate surfaces
as 500 (``no_default_preset``) and a failing gate surfaces as 404. The
plan documents this contract.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubebox.credentials.encryption import FernetBackend
from cubebox.db.engine import _build_database_url
from cubebox.repositories.user_sandbox import UserSandboxRepository
from cubebox.sandbox.manager import SandboxManager

pytestmark = pytest.mark.e2e


FourLayerFixture = tuple[
    tuple[httpx.AsyncClient, str, str],
    tuple[httpx.AsyncClient, str, str],
]


_ENCRYPTION_BACKEND = FernetBackend([Fernet.generate_key()])


class TestGroupChatMessaging:
    """Send-message access control and metadata for topic conversations."""

    @pytest.mark.anyio
    async def test_member_can_send_message_in_topic_conversation(
        self, four_layer_admin_and_member: FourLayerFixture
    ) -> None:
        """A topic participant can POST to /messages — the participant gate passes.

        Without an LLM preset seeded the run errors out at resolve_preset with
        500 ``no_default_preset``. The point of the test is that the gate did
        NOT short-circuit with 404, proving the member is authorised.
        """
        (admin_c, ws_id, _), (member_c, _, member_uid) = four_layer_admin_and_member

        resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/topics",
            json={
                "title": "Member Send Topic",
                "member_user_ids": [member_uid],
            },
        )
        assert resp.status_code == 201, resp.text
        conv_id = resp.json()["conversation"]["id"]

        msg_resp = await member_c.post(
            f"/api/v1/ws/{ws_id}/conversations/{conv_id}/messages",
            json={"content": "Hello from member"},
        )
        assert msg_resp.status_code != 404, msg_resp.text

    @pytest.mark.anyio
    async def test_removed_member_cannot_send_message(
        self, four_layer_admin_and_member: FourLayerFixture
    ) -> None:
        """A removed participant gets 404 (info-disclosure safe) on send."""
        (admin_c, ws_id, _), (member_c, _, member_uid) = four_layer_admin_and_member

        resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/topics",
            json={"title": "Eviction", "member_user_ids": [member_uid]},
        )
        assert resp.status_code == 201, resp.text
        topic_id = resp.json()["topic"]["id"]
        conv_id = resp.json()["conversation"]["id"]

        # Owner removes member.
        rm = await admin_c.delete(f"/api/v1/ws/{ws_id}/topics/{topic_id}/participants/{member_uid}")
        assert rm.status_code == 204, rm.text

        # Member tries to send — must be 404 (not 403) so the response is
        # indistinguishable from "conversation doesn't exist".
        msg_resp = await member_c.post(
            f"/api/v1/ws/{ws_id}/conversations/{conv_id}/messages",
            json={"content": "still here?"},
        )
        assert msg_resp.status_code == 404, msg_resp.text

    @pytest.mark.anyio
    async def test_archived_topic_conversation_inaccessible(
        self, four_layer_admin_and_member: FourLayerFixture
    ) -> None:
        """After topic delete, the conversation is invisible to all participants."""
        (admin_c, ws_id, _), (member_c, _, member_uid) = four_layer_admin_and_member

        resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/topics",
            json={"title": "Doomed", "member_user_ids": [member_uid]},
        )
        assert resp.status_code == 201, resp.text
        topic_id = resp.json()["topic"]["id"]
        conv_id = resp.json()["conversation"]["id"]

        # Both can see before archive.
        before_admin = await admin_c.get(f"/api/v1/ws/{ws_id}/conversations/{conv_id}")
        assert before_admin.status_code == 200, before_admin.text
        before_member = await member_c.get(f"/api/v1/ws/{ws_id}/conversations/{conv_id}")
        assert before_member.status_code == 200, before_member.text

        # Owner archives the topic.
        delete_resp = await admin_c.delete(f"/api/v1/ws/{ws_id}/topics/{topic_id}")
        assert delete_resp.status_code == 204, delete_resp.text

        # Both lose access.
        get_admin = await admin_c.get(f"/api/v1/ws/{ws_id}/conversations/{conv_id}")
        assert get_admin.status_code == 404, get_admin.text
        get_member = await member_c.get(f"/api/v1/ws/{ws_id}/conversations/{conv_id}")
        assert get_member.status_code == 404, get_member.text

        # And the conversation is hidden from the listing.
        list_admin = await admin_c.get(f"/api/v1/ws/{ws_id}/conversations")
        if list_admin.status_code == 200:
            ids = [c["id"] for c in list_admin.json().get("items", [])]
            assert conv_id not in ids


class TestGroupChatRunContextResolution:
    """``_resolve_topic_run_context`` drives the per-send is_group_chat /
    sender attribution / sandbox_mode decisions. Verifies it returns the
    right shape for the cases the run_manager branches on.
    """

    @pytest.mark.anyio
    async def test_group_chat_flag_and_sender_set_for_topic_conversation(
        self, four_layer_admin_and_member: FourLayerFixture
    ) -> None:
        """For a topic conversation with >1 participant, the resolver returns
        ``is_group_chat=True`` and a ``sender_display_name``. The run_manager
        guards memory-snapshot injection behind ``not ctx.is_group_chat`` —
        so this resolution is the structural seam for the "personal memory
        skipped in group chat" behaviour.
        """
        from cubebox.api.routes.v1.conversations import _resolve_topic_run_context
        from cubebox.auth.context import RequestContext
        from cubebox.models.conversation import Conversation

        (admin_c, ws_id, _), (_, _, member_uid) = four_layer_admin_and_member
        resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/topics",
            json={
                "title": "Resolver Probe",
                "member_user_ids": [member_uid],
                "sandbox_mode": "dedicated",
            },
        )
        assert resp.status_code == 201, resp.text
        topic_data = resp.json()
        conv_id = topic_data["conversation"]["id"]
        topic_id = topic_data["topic"]["id"]

        engine = create_async_engine(_build_database_url(), poolclass=NullPool)
        try:
            maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with maker() as session:
                from cubebox.models import User

                conv_row = await session.get(Conversation, conv_id)
                assert conv_row is not None
                # Reach into the owner row for org_id and to load the user
                # we'll pretend is the sender (admin in this case).
                org_id = conv_row.org_id
                creator_user_id = conv_row.creator_user_id
                user_row = await session.get(User, creator_user_id)
                assert user_row is not None
        finally:
            await engine.dispose()

        from cubebox.models import Role

        ctx = RequestContext(
            user=user_row,
            org_id=org_id,
            workspace_id=ws_id,
            role=Role.ADMIN,
        )
        (
            topic_id_out,
            is_group_chat,
            participant_ids,
            sender_display_name,
            sandbox_mode,
            topic_creator_user_id,
        ) = await _resolve_topic_run_context(conv_row, ctx)

        assert topic_id_out == topic_id
        assert is_group_chat is True
        assert participant_ids is not None
        assert set(participant_ids) == {creator_user_id, member_uid}
        # The sender display falls back to email when display_name is unset
        # — for the test admin, that's an "@example.com" string.
        assert sender_display_name is not None and "@" in sender_display_name
        assert sandbox_mode == "dedicated"
        assert topic_creator_user_id == creator_user_id

    @pytest.mark.anyio
    async def test_personal_conversation_resolver_returns_no_group_chat(
        self, four_layer_admin_and_member: FourLayerFixture
    ) -> None:
        """Personal (non-topic) conversations: resolver returns no group-chat
        signal — guaranteeing the memory-snapshot branch fires and sender
        attribution is NOT applied.
        """
        from cubebox.api.routes.v1.conversations import _resolve_topic_run_context
        from cubebox.auth.context import RequestContext
        from cubebox.models.conversation import Conversation

        (admin_c, ws_id, _), _ = four_layer_admin_and_member
        resp = await admin_c.post(f"/api/v1/ws/{ws_id}/conversations", params={"title": "Solo"})
        assert resp.status_code == 201, resp.text
        conv_id = resp.json()["id"]

        engine = create_async_engine(_build_database_url(), poolclass=NullPool)
        try:
            maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with maker() as session:
                from cubebox.models import User

                conv_row = await session.get(Conversation, conv_id)
                assert conv_row is not None
                user_row = await session.get(User, conv_row.creator_user_id)
                assert user_row is not None
                org_id = conv_row.org_id
        finally:
            await engine.dispose()

        from cubebox.models import Role

        ctx = RequestContext(
            user=user_row,
            org_id=org_id,
            workspace_id=ws_id,
            role=Role.ADMIN,
        )
        (
            topic_id,
            is_group_chat,
            participant_ids,
            sender_display_name,
            sandbox_mode,
            topic_creator_user_id,
        ) = await _resolve_topic_run_context(conv_row, ctx)

        assert topic_id is None
        assert is_group_chat is False
        assert participant_ids is None
        assert sender_display_name is None
        assert sandbox_mode is None
        assert topic_creator_user_id is None


class TestDedicatedSandboxIsolation:
    """Topic-scope sandbox keying isolates topic sandboxes from any
    participant's personal sandbox row. Asserted at the SandboxManager
    layer so the test doesn't need a working LLM provider.
    """

    @pytest.mark.anyio
    async def test_dedicated_sandbox_is_isolated_from_creators_personal(
        self,
        fake_opensandbox: None,
        session_factory: Any,
        seeded_org_ws_user: tuple[str, str, str, str],
    ) -> None:
        """With sandbox_mode=dedicated the run keys by topic_id, producing
        a DB row distinct from the creator's personal (topic_id IS NULL) row.
        """
        del fake_opensandbox
        org_id, ws_a, _ws_b, user_id = seeded_org_ws_user
        mgr = SandboxManager(session_factory, _ENCRYPTION_BACKEND)

        # Create a real topic row first — user_sandboxes.topic_id has an FK
        # to topics.id, so a synthetic id would raise IntegrityError on
        # reserve and the manager would mis-interpret it as a lost race.
        from cubebox.models.topic import Topic

        async with session_factory() as s:
            topic_row = Topic(
                org_id=org_id,
                workspace_id=ws_a,
                title="Dedicated Iso Probe",
                creator_user_id=user_id,
                sandbox_mode="dedicated",
            )
            s.add(topic_row)
            await s.commit()
            topic_id = topic_row.id

        # Personal sandbox (scope_type='user') — analog of "creator's personal".
        await mgr.get_or_create(
            scope_type="user",
            scope_id=user_id,
            user_id=user_id,
            org_id=org_id,
            workspace_id=ws_a,
        )

        # Dedicated topic sandbox (scope_type='topic') — should NOT collide.
        await mgr.get_or_create(
            scope_type="topic",
            scope_id=topic_id,
            user_id=user_id,
            org_id=org_id,
            workspace_id=ws_a,
        )

        async with session_factory() as s:
            repo = UserSandboxRepository(s, org_id=org_id, workspace_id=ws_a)
            personal = await repo.get_active_by_scope(scope_type="user", scope_id=user_id)
            dedicated = await repo.get_active_by_scope(scope_type="topic", scope_id=topic_id)

        assert personal is not None
        assert dedicated is not None
        assert personal.id != dedicated.id
        assert personal.sandbox_id != dedicated.sandbox_id
        assert (personal.scope_type, personal.scope_id) == ("user", user_id)
        assert (dedicated.scope_type, dedicated.scope_id) == ("topic", topic_id)

        # Cross-check via raw SQL: exactly TWO active rows (one personal,
        # one topic) live for this user-workspace pair.
        async with session_factory() as s:
            rows = (
                await s.execute(
                    sa.text(
                        "SELECT scope_type, scope_id FROM user_sandboxes "
                        "WHERE user_id=:u AND workspace_id=:w AND status='running'"
                    ),
                    {"u": user_id, "w": ws_a},
                )
            ).all()
        scope_keys = {(r[0], r[1]) for r in rows}
        assert scope_keys == {("user", user_id), ("topic", topic_id)}

    @pytest.mark.anyio
    async def test_creator_sandbox_mode_reuses_personal(
        self,
        fake_opensandbox: None,
        session_factory: Any,
        seeded_org_ws_user: tuple[str, str, str, str],
    ) -> None:
        """sandbox_mode=creator routes the run through the topic creator's
        personal sandbox (no topic_id), so a second get_or_create with no
        topic_id reuses the same row instead of provisioning a duplicate.

        run_manager translates ``sandbox_mode='creator'`` into:
            sandbox_user_id = topic_creator_user_id
            topic_id        = None  # personal keying
        — which is exactly what we drive here.
        """
        del fake_opensandbox
        org_id, ws_a, _ws_b, user_id = seeded_org_ws_user
        mgr = SandboxManager(session_factory, _ENCRYPTION_BACKEND)

        await mgr.get_or_create(
            scope_type="user",
            scope_id=user_id,
            user_id=user_id,
            org_id=org_id,
            workspace_id=ws_a,
        )
        # Second call with the same identity reuses the existing row.
        await mgr.get_or_create(
            scope_type="user",
            scope_id=user_id,
            user_id=user_id,
            org_id=org_id,
            workspace_id=ws_a,
        )

        async with session_factory() as s:
            count = (
                await s.execute(
                    sa.text(
                        "SELECT COUNT(*) FROM user_sandboxes "
                        "WHERE user_id=:u AND workspace_id=:w "
                        "AND scope_type='user' AND scope_id=:s "
                        "AND status='running'"
                    ),
                    {"u": user_id, "w": ws_a, "s": user_id},
                )
            ).scalar_one()
        assert count == 1


class TestIMResumeRefusesTopicConversation:
    """The IM resume path explicitly refuses topic conversations in v1
    so a participant clicking a card in a non-topic conversation can't
    accidentally answer a topic HITL. The guard lives in
    ``cubebox.im.resume.resume_paused_run`` after
    ``_resolve_run_context`` returns a non-null ``topic_id``.
    """

    @pytest.mark.anyio
    async def test_resume_paused_run_refuses_topic(self) -> None:
        from cubebox.im import resume as resume_mod

        async def _fake_resolve(run_id: str) -> tuple[str, str, str, str, str | None]:
            return ("conv-topic", "user-1", "org-1", "ws-1", "top-1")

        run_manager = AsyncMock()
        run_manager.resume_run_with_answer = AsyncMock()

        with patch.object(resume_mod, "_resolve_run_context", _fake_resolve):
            ok = await resume_mod.resume_paused_run(
                run_id="run-fake",
                input_kind="sandbox_confirm",
                choice="approve",
                operator_open_id="op-1",
                run_manager=run_manager,
            )
        assert ok is False
        # The guard returned before any resume call.
        run_manager.resume_run_with_answer.assert_not_called()

    @pytest.mark.anyio
    async def test_resume_paused_run_allows_personal_conversation(self) -> None:
        """Counterpart: a non-topic conversation must NOT trigger the guard."""
        from cubebox.im import resume as resume_mod

        async def _fake_resolve(run_id: str) -> tuple[str, str, str, str, str | None]:
            return ("conv-personal", "user-1", "org-1", "ws-1", None)

        run_manager = AsyncMock()
        run_manager.resume_run_with_answer = AsyncMock(return_value=None)

        with patch.object(resume_mod, "_resolve_run_context", _fake_resolve):
            ok = await resume_mod.resume_paused_run(
                run_id="run-fake",
                input_kind="sandbox_confirm",
                choice="approve",
                operator_open_id="op-1",
                run_manager=run_manager,
            )
        assert ok is True
        run_manager.resume_run_with_answer.assert_awaited_once()


class TestSenderAttributionViaCubepi:
    """Sender attribution moved to cubepi (provider boundary). Cubebox sets
    ``metadata.sender_display_name`` on the UserMessage and cubepi's
    ``apply_sender_attribution`` rewrites the first text block. This smoke
    test pins the contract so a bad cubepi pin bump fails here, not deep
    inside a provider call.
    """

    @pytest.mark.anyio
    async def test_apply_sender_attribution_prefixes_first_text_block(self) -> None:
        from cubepi.providers.base import (
            TextContent,
            UserMessage,
            apply_sender_attribution,
        )

        msg = UserMessage(
            content=[TextContent(text="Hello team")],
            metadata={"sender_display_name": "Alice", "sender_user_id": "u-1"},
        )
        out = apply_sender_attribution(msg, msg.content)
        assert len(out) == 1
        first = out[0]
        assert isinstance(first, TextContent)
        assert first.text == "[Alice]: Hello team"

    @pytest.mark.anyio
    async def test_apply_sender_attribution_noop_without_metadata(self) -> None:
        from cubepi.providers.base import (
            TextContent,
            UserMessage,
            apply_sender_attribution,
        )

        msg = UserMessage(content=[TextContent(text="Hello")])
        out = apply_sender_attribution(msg, msg.content)
        assert out == msg.content
