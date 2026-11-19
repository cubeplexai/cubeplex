"""Unit tests for IM /stop and /steer command parsing."""

from __future__ import annotations

import pytest

from cubeplex.im.control_commands import (
    SteerCommand,
    StopCommand,
    make_im_client_steer_id,
    parse_control_command,
)


def test_parse_stop_command_variants() -> None:
    assert parse_control_command("/stop") == StopCommand()
    assert parse_control_command("  /STOP \n") == StopCommand()
    assert parse_control_command("/stopped") is None
    assert parse_control_command("/stop now") is None


def test_parse_steer_command_variants() -> None:
    assert parse_control_command("/steer") == SteerCommand(content="")
    assert parse_control_command("  /steer   ") == SteerCommand(content="")
    assert parse_control_command("/STEER check line 1\nline 2") == SteerCommand(
        content="check line 1\nline 2"
    )
    assert parse_control_command("/steering left") is None
    assert parse_control_command("hello /steer") is None


def test_make_im_client_steer_id_is_deterministic_and_bounded() -> None:
    first = make_im_client_steer_id("acct-1", "evt-1")
    second = make_im_client_steer_id("acct-1", "evt-1")
    other = make_im_client_steer_id("acct-1", "evt-2")
    assert first == second
    assert first != other
    assert first.startswith("im:")
    assert len(first) <= 64


async def test_discord_slash_shared_thread_scope_matches_connector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from cubeplex.im.discord.commands import _resolve_discord_scope
    from cubeplex.im.types import make_channel_scope, make_thread_scope

    async def _fake_lookup(_session_maker: object, _account_id: str, lookup_channel_id: str) -> str:
        assert lookup_channel_id == "parent-chan"
        return "shared"

    monkeypatch.setattr("cubeplex.im.types.lookup_binding_mode", _fake_lookup)

    thread_interaction = SimpleNamespace(
        channel=SimpleNamespace(
            id=999,
            parent_id="parent-chan",
            type=SimpleNamespace(value=11),
        ),
        user=SimpleNamespace(id=42),
    )
    res_thread = await _resolve_discord_scope(
        thread_interaction,  # type: ignore[arg-type]
        account_id="acct-1",
        session_maker=object(),
    )
    assert res_thread == ("999", "42", make_thread_scope("999"), "thread")

    channel_interaction = SimpleNamespace(
        channel=SimpleNamespace(
            id="parent-chan",
            parent_id=None,
            type=SimpleNamespace(value=0),
        ),
        user=SimpleNamespace(id=42),
    )
    res_chan = await _resolve_discord_scope(
        channel_interaction,  # type: ignore[arg-type]
        account_id="acct-1",
        session_maker=object(),
    )
    assert res_chan == ("parent-chan", "42", make_channel_scope(), "channel")


async def test_ephemeral_rejection_notifier_forwards_messages() -> None:
    from cubeplex.im.control_commands import EphemeralRejectionNotifier

    sent: list[str] = []

    async def _reply(text: str) -> None:
        sent.append(text)

    notifier = EphemeralRejectionNotifier(_reply)
    await notifier.send_to_chat("c1", None, "hello")
    await notifier.send_link_required("c1", None, "u1")
    await notifier.send_not_member("c1", None, "u1")
    assert len(sent) == 3
    assert sent[0] == "hello"
    assert "/link" in sent[1]


async def test_discord_slash_stop_and_steer_handlers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from cubeplex.im.control_commands import (
        STEER_IDLE_FALLBACK_SLASH_REPLY,
        STEER_USAGE_REPLY,
    )
    from cubeplex.im.discord.commands import _handle_steer, _handle_stop
    from cubeplex.im.inbound import IngestResult

    class _FakeSession:
        async def __aenter__(self) -> _FakeSession:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, _model: object, _id: str) -> object:
            return SimpleNamespace(id="acct-1", external_account_id="bot-1")

    bot = SimpleNamespace(
        _cubeplex_session_maker=lambda: _FakeSession(),
        _cubeplex_account_id="acct-1",
        _cubeplex_run_manager=object(),
    )

    def _make_interaction() -> SimpleNamespace:
        return SimpleNamespace(
            id=12345,
            channel=SimpleNamespace(id=77, parent_id=None, type=SimpleNamespace(value=0)),
            user=SimpleNamespace(id=99),
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    monkeypatch.setattr(
        "cubeplex.im.discord.commands._resolve_discord_scope",
        AsyncMock(return_value=("77", "99", "ch", "channel")),
    )

    # 1. /stop handled
    monkeypatch.setattr(
        "cubeplex.im.control_commands.maybe_handle_control_command",
        AsyncMock(return_value=True),
    )
    inter_stop = _make_interaction()
    await _handle_stop(inter_stop, bot)  # type: ignore[arg-type]
    inter_stop.response.defer.assert_awaited_once_with(ephemeral=True)

    # 2. bare /steer returns usage hint
    inter_bare = _make_interaction()
    await _handle_steer(inter_bare, bot, message="   ")  # type: ignore[arg-type]
    inter_bare.followup.send.assert_awaited_once_with(STEER_USAGE_REPLY, ephemeral=True)

    # 3. /steer idle fallback calls ingest_inbound_event and replies ephemerally
    monkeypatch.setattr(
        "cubeplex.im.control_commands.maybe_handle_control_command",
        AsyncMock(return_value=False),
    )
    ingest_mock = AsyncMock(return_value=IngestResult(outcome="enqueued", conversation_id="c1"))
    monkeypatch.setattr("cubeplex.im.inbound.ingest_inbound_event", ingest_mock)
    inter_steer = _make_interaction()
    await _handle_steer(inter_steer, bot, message="run tests")  # type: ignore[arg-type]
    ingest_mock.assert_awaited_once()
    inter_steer.followup.send.assert_awaited_once_with(
        STEER_IDLE_FALLBACK_SLASH_REPLY, ephemeral=True
    )

    # 4. Exception path sends ephemeral error instead of hanging
    monkeypatch.setattr(
        "cubeplex.im.control_commands.maybe_handle_control_command",
        AsyncMock(side_effect=RuntimeError("boom")),
    )
    inter_err = _make_interaction()
    await _handle_stop(inter_err, bot)  # type: ignore[arg-type]
    inter_err.followup.send.assert_awaited_once()

    # 5. Exception path in _handle_steer
    inter_steer_err = _make_interaction()
    await _handle_steer(inter_steer_err, bot, message="x")  # type: ignore[arg-type]
    inter_steer_err.followup.send.assert_awaited_once()

    # 6. Missing bot metadata / missing channel / missing account
    bad_bot = SimpleNamespace()
    inter_bad = _make_interaction()
    await _handle_stop(inter_bad, bad_bot)  # type: ignore[arg-type]
    await _handle_steer(inter_bad, bad_bot, message="x")  # type: ignore[arg-type]
    assert inter_bad.followup.send.await_count == 2


async def test_discord_scope_isolated_dm_and_missing_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from cubeplex.im.discord.commands import _resolve_discord_scope
    from cubeplex.im.types import (
        DM_SCOPE_KEY,
        make_participant_scope,
        make_thread_participant_scope,
    )

    none_inter = SimpleNamespace(channel=None, user=SimpleNamespace(id=1))
    assert (
        await _resolve_discord_scope(
            none_inter,
            account_id="a1",
            session_maker=object(),  # type: ignore[arg-type]
        )
        is None
    )

    dm_inter = SimpleNamespace(
        channel=SimpleNamespace(id=10, type=SimpleNamespace(value=1)),
        user=SimpleNamespace(id=20),
    )
    assert await _resolve_discord_scope(
        dm_inter,
        account_id="a1",
        session_maker=object(),  # type: ignore[arg-type]
    ) == ("10", "20", DM_SCOPE_KEY, "dm")

    async def _iso(_sm: object, _aid: str, _cid: str) -> str:
        return "isolated"

    monkeypatch.setattr("cubeplex.im.types.lookup_binding_mode", _iso)
    iso_thread = SimpleNamespace(
        channel=SimpleNamespace(id=30, parent_id=5, type=SimpleNamespace(value=12)),
        user=SimpleNamespace(id=20),
    )
    assert await _resolve_discord_scope(
        iso_thread,
        account_id="a1",
        session_maker=object(),  # type: ignore[arg-type]
    ) == ("30", "20", make_thread_participant_scope("20", "30"), "thread")

    iso_chan = SimpleNamespace(
        channel=SimpleNamespace(id=31, parent_id=None, type=SimpleNamespace(value=0)),
        user=SimpleNamespace(id=20),
    )
    assert await _resolve_discord_scope(
        iso_chan,
        account_id="a1",
        session_maker=object(),  # type: ignore[arg-type]
    ) == ("31", "20", make_participant_scope("20"), "channel")


async def test_slack_slash_stop_and_steer_handlers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from cubeplex.im.control_commands import (
        STEER_IDLE_FALLBACK_SLASH_REPLY,
        STEER_USAGE_REPLY,
    )
    from cubeplex.im.inbound import IngestResult
    from cubeplex.im.slack.commands import (
        _handle_steer_slash,
        _handle_stop_slash,
        _resolve_slack_slash_scope,
        register_commands,
    )
    from cubeplex.im.types import DM_SCOPE_KEY, make_channel_scope, make_participant_scope

    # Scope helper: DM, shared channel, isolated channel, missing
    assert await _resolve_slack_slash_scope({}, account_id="a1", session_maker=object()) is None
    assert await _resolve_slack_slash_scope(
        {"channel_id": "D123", "user_id": "U1"}, account_id="a1", session_maker=object()
    ) == ("D123", "U1", DM_SCOPE_KEY, "dm")

    monkeypatch.setattr("cubeplex.im.types.lookup_binding_mode", AsyncMock(return_value="shared"))
    assert await _resolve_slack_slash_scope(
        {"channel_id": "C123", "user_id": "U1"}, account_id="a1", session_maker=object()
    ) == ("C123", "U1", make_channel_scope(), "channel")

    monkeypatch.setattr("cubeplex.im.types.lookup_binding_mode", AsyncMock(return_value="isolated"))
    assert await _resolve_slack_slash_scope(
        {"channel_id": "C123", "user_id": "U1"}, account_id="a1", session_maker=object()
    ) == ("C123", "U1", make_participant_scope("U1"), "channel")

    class _FakeSession:
        def __init__(self, acct: object) -> None:
            self._acct = acct

        async def __aenter__(self) -> _FakeSession:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, _model: object, _id: str) -> object:
            return self._acct

    acct = SimpleNamespace(id="a1", external_account_id="B1")
    respond = AsyncMock()

    # Missing channel/user
    await _handle_stop_slash(
        {}, respond, account_id="a1", session_maker=lambda: _FakeSession(acct), run_manager=None
    )
    await _handle_steer_slash(
        {}, respond, account_id="a1", session_maker=lambda: _FakeSession(acct), run_manager=None
    )
    assert respond.await_count == 2

    # Account not found
    respond.reset_mock()
    await _handle_stop_slash(
        {"channel_id": "D1", "user_id": "U1"},
        respond,
        account_id="a1",
        session_maker=lambda: _FakeSession(None),
        run_manager=None,
    )
    respond.assert_awaited_once_with("IM account not found.", response_type="ephemeral")

    # Bare /steer
    respond.reset_mock()
    await _handle_steer_slash(
        {"channel_id": "D1", "user_id": "U1", "text": "  "},
        respond,
        account_id="a1",
        session_maker=lambda: _FakeSession(acct),
        run_manager=None,
    )
    respond.assert_awaited_once_with(STEER_USAGE_REPLY, response_type="ephemeral")

    # /stop happy path + exception path
    monkeypatch.setattr(
        "cubeplex.im.control_commands.maybe_handle_control_command",
        AsyncMock(return_value=True),
    )
    await _handle_stop_slash(
        {"channel_id": "D1", "user_id": "U1", "trigger_id": "t1"},
        respond,
        account_id="a1",
        session_maker=lambda: _FakeSession(acct),
        run_manager=None,
    )

    monkeypatch.setattr(
        "cubeplex.im.control_commands.maybe_handle_control_command",
        AsyncMock(side_effect=RuntimeError("boom")),
    )
    respond.reset_mock()
    await _handle_stop_slash(
        {"channel_id": "D1", "user_id": "U1", "trigger_id": "t2"},
        respond,
        account_id="a1",
        session_maker=lambda: _FakeSession(acct),
        run_manager=None,
    )
    respond.assert_awaited_once_with("Failed to stop current reply.", response_type="ephemeral")

    # /steer idle fallback + exception path
    monkeypatch.setattr(
        "cubeplex.im.control_commands.maybe_handle_control_command",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "cubeplex.im.inbound.ingest_inbound_event",
        AsyncMock(return_value=IngestResult(outcome="enqueued", conversation_id="c1")),
    )
    respond.reset_mock()
    await _handle_steer_slash(
        {"channel_id": "D1", "user_id": "U1", "text": "hi", "trigger_id": "t3"},
        respond,
        account_id="a1",
        session_maker=lambda: _FakeSession(acct),
        run_manager=None,
    )
    respond.assert_awaited_once_with(STEER_IDLE_FALLBACK_SLASH_REPLY, response_type="ephemeral")

    # register_commands wires /stop and /steer
    registered: dict[str, object] = {}

    class _FakeSlackApp:
        def command(self, name: str) -> object:
            def _dec(fn: object) -> object:
                registered[name] = fn
                return fn

            return _dec

    register_commands(
        _FakeSlackApp(),
        account_id="a1",
        workspace_id="w1",
        session_maker=lambda: _FakeSession(acct),
        run_manager=None,
    )
    assert {"/link", "/new", "/reset", "/stop", "/steer"} <= set(registered)


async def test_control_commands_error_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from cubeplex.im.control_commands import (
        STEER_QUEUE_FULL_REPLY,
        STEER_TOO_LARGE_REPLY,
        _safe_reply,
        execute_steer_command,
        maybe_handle_control_command,
    )
    from cubeplex.im.types import InboundEvent
    from cubeplex.services.conversation_steering import SteerAttemptResult

    # _safe_reply swallows exceptions
    await _safe_reply(AsyncMock(side_effect=RuntimeError("send failed")), "hi")

    # missing platform_event_id returns True immediately
    evt_no_id = InboundEvent(
        platform="feishu",
        platform_event_id="",
        account_external_id="a",
        channel_id="c",
        scope_key="ch",
        scope_kind="channel",
        sender_ref="u",
        sender_open_id="u",
        text="/stop",
        reply_to_id=None,
        inbound_message_id="",
    )
    assert (
        await maybe_handle_control_command(
            evt_no_id,
            account=SimpleNamespace(id="a1"),  # type: ignore[arg-type]
            session_maker=None,  # type: ignore[arg-type]
            run_manager=None,
            reply=AsyncMock(),
        )
        is True
    )

    # execute_steer_command maps queue_full and too_large
    monkeypatch.setattr(
        "cubeplex.im.control_commands.find_live_im_conversation",
        AsyncMock(return_value=SimpleNamespace(id="conv-1")),
    )
    rm = SimpleNamespace(redis=object(), key_prefix="p")
    acct = SimpleNamespace(id="a1", org_id="o1", workspace_id="w1")
    for outcome, expected in (
        ("queue_full", STEER_QUEUE_FULL_REPLY),
        ("too_large", STEER_TOO_LARGE_REPLY),
    ):
        monkeypatch.setattr(
            "cubeplex.im.control_commands.steer_conversation_run",
            AsyncMock(
                return_value=SteerAttemptResult(
                    outcome=outcome,  # type: ignore[arg-type]
                    http_status=outcome,
                    run_id=None,
                    steer_id="s1",
                )
            ),
        )
        res = await execute_steer_command(
            session=None,  # type: ignore[arg-type]
            account=acct,  # type: ignore[arg-type]
            effective_user_id="u1",
            channel_id="c1",
            scope_key="ch",
            scope_kind="channel",
            platform_event_id="e1",
            content="hi",
            has_attachments=False,
            run_manager=rm,  # type: ignore[arg-type]
        )
        assert res == ("handled", expected)


async def test_control_commands_stop_and_lookup_edge_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import asynccontextmanager
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from cubeplex.im.control_commands import (
        STOP_NO_ACTIVE_REPLY,
        _find_stop_target_run_id,
        execute_stop_command,
        find_live_im_conversation,
    )

    class _ExecResult:
        def __init__(self, val: object) -> None:
            self._val = val

        def scalar_one_or_none(self) -> object:
            return self._val

    class _FakeSavepoint:
        def __init__(self) -> None:
            self.rolled_back = False

        async def __aenter__(self) -> _FakeSavepoint:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def rollback(self) -> None:
            self.rolled_back = True

    class _FakeSession:
        def __init__(self, link: object, conv: object, topic: object = None) -> None:
            self._link = link
            self._conv = conv
            self._topic = topic
            self.savepoint = _FakeSavepoint()

        async def execute(self, _stmt: object) -> _ExecResult:
            return _ExecResult(self._link)

        async def get(self, model: object, _id: str) -> object:
            from cubeplex.models.conversation import Conversation

            if model is Conversation:
                return self._conv
            return self._topic

        def begin_nested(self) -> _FakeSavepoint:
            return self.savepoint

    acct = SimpleNamespace(id="a1", org_id="o1", workspace_id="w1", config={})
    # 1. Deleted conversation returns None
    res_del = await find_live_im_conversation(
        _FakeSession(
            SimpleNamespace(conversation_id="c1"), SimpleNamespace(deleted_at=datetime.now(UTC))
        ),  # type: ignore[arg-type]
        acct,  # type: ignore[arg-type]
        channel_id="ch1",
        scope_key="ch",
        scope_kind="channel",
        effective_user_id="u1",
    )
    assert res_del is None

    # 2. Archived topic returns None when wants_topic is True
    res_arch = await find_live_im_conversation(
        _FakeSession(
            SimpleNamespace(conversation_id="c1"),
            SimpleNamespace(id="c1", deleted_at=None, topic_id="t1"),
            SimpleNamespace(id="t1", is_archived=True),
        ),  # type: ignore[arg-type]
        acct,  # type: ignore[arg-type]
        channel_id="ch1",
        scope_key="ch",
        scope_kind="channel",
        effective_user_id="u1",
    )
    assert res_arch is None

    # 3. _find_stop_target_run_id uses checkpointer pending_run_id
    @asynccontextmanager
    async def _fake_cp() -> object:
        yield SimpleNamespace(load_pending_run_id=AsyncMock(return_value="run-pending-1"))

    monkeypatch.setattr("cubeplex.im.control_commands.shared_checkpointer", _fake_cp)
    assert (
        await _find_stop_target_run_id(
            _FakeSession(None, None),  # type: ignore[arg-type]
            account=acct,  # type: ignore[arg-type]
            conversation_id="c1",
            run_manager=None,
        )
        == "run-pending-1"
    )

    # 4. execute_stop_command rolls back savepoint on LookupError and finished admission
    monkeypatch.setattr(
        "cubeplex.im.control_commands.find_live_im_conversation",
        AsyncMock(return_value=SimpleNamespace(id="c1")),
    )
    monkeypatch.setattr(
        "cubeplex.im.control_commands._find_stop_target_run_id",
        AsyncMock(return_value="r1"),
    )
    sess = _FakeSession(None, None)

    class _LookupExecSvc:
        def __init__(self, *_a: object, **_kw: object) -> None:
            pass

        async def stop_run(self, **_kw: object) -> object:
            raise LookupError("missing")

    monkeypatch.setattr("cubeplex.im.control_commands.ConversationExecutionService", _LookupExecSvc)
    stop_res = await execute_stop_command(
        session=sess,  # type: ignore[arg-type]
        account=acct,  # type: ignore[arg-type]
        effective_user_id="u1",
        channel_id="ch1",
        scope_key="ch",
        scope_kind="channel",
        run_manager=None,
    )
    assert stop_res.reply_text == STOP_NO_ACTIVE_REPLY
    assert sess.savepoint.rolled_back is True

    class _FinishedExecSvc:
        def __init__(self, *_a: object, **_kw: object) -> None:
            pass

        async def stop_run(self, **_kw: object) -> object:
            return SimpleNamespace(
                run_id="r1",
                admission=SimpleNamespace(run_finished_at=datetime.now(UTC)),
            )

    sess2 = _FakeSession(None, None)
    monkeypatch.setattr(
        "cubeplex.im.control_commands.ConversationExecutionService", _FinishedExecSvc
    )
    stop_res2 = await execute_stop_command(
        session=sess2,  # type: ignore[arg-type]
        account=acct,  # type: ignore[arg-type]
        effective_user_id="u1",
        channel_id="ch1",
        scope_key="ch",
        scope_kind="channel",
        run_manager=None,
    )
    assert stop_res2.reply_text == STOP_NO_ACTIVE_REPLY
    assert sess2.savepoint.rolled_back is True


async def test_discord_register_commands_wires_stop_and_steer() -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from cubeplex.im.discord.commands import register_commands

    registered: list[str] = []

    class _FakeTree:
        def command(self, *, name: str, description: str) -> object:
            del description

            def _dec(fn: object) -> object:
                registered.append(name)
                return fn

            return _dec

        async def sync(self) -> list[str]:
            return registered

    bot = SimpleNamespace(tree=_FakeTree(), get_cog=AsyncMock())
    await register_commands(bot)  # type: ignore[arg-type]
    assert {"new", "reset", "link", "stop", "steer", "models", "model"} <= set(registered)


def test_parse_model_commands_variants() -> None:
    from cubeplex.im.control_commands import ListModelsCommand, SwitchModelCommand

    assert parse_control_command("/models") == ListModelsCommand()
    assert parse_control_command("  /MODELS \n") == ListModelsCommand()
    assert parse_control_command("/models extra") is None
    assert parse_control_command("/model") == SwitchModelCommand(model="")
    assert parse_control_command("  /model  ") == SwitchModelCommand(model="")
    assert parse_control_command("/MODEL  pro ") == SwitchModelCommand(model="pro")
    assert parse_control_command("/model openai/gpt-4o") == SwitchModelCommand(
        model="openai/gpt-4o"
    )
    assert parse_control_command("/modeling") is None


def test_model_matching_and_active_key_resolution() -> None:
    from cubeplex.im.control_commands import _match_model_preset, _resolve_active_model_key
    from cubeplex.llm.snapshot import ModelPreset

    presets = (
        ModelPreset(
            key="flash",
            primary="openai/gpt-4o-mini",
            fallbacks=(),
            kind="tier",
            is_default=True,
        ),
        ModelPreset(
            key="pro",
            primary="anthropic/claude-sonnet-4",
            fallbacks=(),
            kind="tier",
            is_default=False,
        ),
        ModelPreset(
            key="dup-a",
            primary="openai/gpt-4o",
            fallbacks=(),
            kind="custom",
            is_default=False,
        ),
        ModelPreset(
            key="dup-b",
            primary="openai/gpt-4o",
            fallbacks=(),
            kind="custom",
            is_default=False,
        ),
    )

    assert _resolve_active_model_key(presets, None) == "flash"
    assert _resolve_active_model_key(presets, "pro") == "pro"
    assert _resolve_active_model_key(presets, "deleted") == "flash"

    assert _match_model_preset(presets, "pro") == presets[1]
    assert _match_model_preset(presets, "PRO") == presets[1]
    assert _match_model_preset(presets, "anthropic/claude-sonnet-4") == presets[1]
    assert _match_model_preset(presets, "openai/gpt-4o") is None
    assert _match_model_preset(presets, "missing") is None


async def test_execute_list_and_switch_model_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import datetime
    from types import SimpleNamespace

    from cubeplex.im.control_commands import (
        MODELS_EMPTY_REPLY,
        execute_list_models_command,
        execute_switch_model_command,
    )
    from cubeplex.llm.snapshot import ModelPreset

    presets = (
        ModelPreset(
            key="flash",
            primary="openai/gpt-4o-mini",
            fallbacks=(),
            kind="tier",
            is_default=True,
        ),
        ModelPreset(
            key="pro",
            primary="anthropic/claude-sonnet-4",
            fallbacks=(),
            kind="tier",
            is_default=False,
            description="Reasoning",
        ),
    )

    loaded_presets: tuple[ModelPreset, ...] = ()

    async def _fake_load_presets(
        _session: object, _org_id: str
    ) -> tuple[tuple[ModelPreset, ...], dict[str, str]]:
        return loaded_presets, {}

    conv_obj: SimpleNamespace | None = None

    async def _fake_find_live(*_args: object, **_kwargs: object) -> object:
        return conv_obj

    created_conv = SimpleNamespace(id="conv-created", model_key=None, updated_at=datetime.now())

    async def _fake_resolve_im(*_args: object, **_kwargs: object) -> object:
        return SimpleNamespace(conversation_id="conv-created")

    class _FakeSession:
        async def get(self, _cls: object, pk: str) -> object:
            return created_conv if pk == "conv-created" else None

        async def flush(self) -> None:
            return None

    monkeypatch.setattr("cubeplex.im.control_commands._load_presets", _fake_load_presets)
    monkeypatch.setattr("cubeplex.im.control_commands.find_live_im_conversation", _fake_find_live)
    monkeypatch.setattr("cubeplex.im.control_commands.resolve_im_conversation", _fake_resolve_im)

    acct = SimpleNamespace(id="acct-1", org_id="org-1", workspace_id="ws-1")
    sess = _FakeSession()

    assert (
        await execute_list_models_command(
            session=sess,  # type: ignore[arg-type]
            account=acct,  # type: ignore[arg-type]
            effective_user_id="u1",
            channel_id="ch1",
            scope_key="dm",
            scope_kind="dm",
        )
        == MODELS_EMPTY_REPLY
    )

    loaded_presets = presets
    listed = await execute_list_models_command(
        session=sess,  # type: ignore[arg-type]
        account=acct,  # type: ignore[arg-type]
        effective_user_id="u1",
        channel_id="ch1",
        scope_key="dm",
        scope_kind="dm",
    )
    assert "flash (openai/gpt-4o-mini) [默认 · 当前使用]" in listed
    assert "pro (anthropic/claude-sonnet-4) — Reasoning" in listed

    usage = await execute_switch_model_command(
        session=sess,  # type: ignore[arg-type]
        account=acct,  # type: ignore[arg-type]
        effective_user_id="u1",
        channel_id="ch1",
        scope_key="dm",
        scope_kind="dm",
        model_arg="",
    )
    assert "当前使用模型：flash" in usage

    not_found = await execute_switch_model_command(
        session=sess,  # type: ignore[arg-type]
        account=acct,  # type: ignore[arg-type]
        effective_user_id="u1",
        channel_id="ch1",
        scope_key="dm",
        scope_kind="dm",
        model_arg="unknown",
    )
    assert "未找到模型预设「unknown」" in not_found

    switched = await execute_switch_model_command(
        session=sess,  # type: ignore[arg-type]
        account=acct,  # type: ignore[arg-type]
        effective_user_id="u1",
        channel_id="ch1",
        scope_key="dm",
        scope_kind="dm",
        model_arg="PRO",
    )
    assert "已将当前会话模型切换为「pro」" in switched
    assert created_conv.model_key == "pro"


async def test_slack_register_commands_wires_models_and_model() -> None:
    from cubeplex.im.slack.commands import register_commands

    registered: list[str] = []

    class _FakeApp:
        def command(self, name: str) -> object:
            def _dec(fn: object) -> object:
                registered.append(name)
                return fn

            return _dec

    register_commands(_FakeApp(), account_id="acct-1", workspace_id="ws-1", session_maker=object())
    assert {"/link", "/new", "/reset", "/stop", "/steer", "/models", "/model"} <= set(registered)
