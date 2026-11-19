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
