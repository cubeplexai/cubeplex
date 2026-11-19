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
