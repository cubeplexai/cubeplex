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
