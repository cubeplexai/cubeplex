"""Slack slash commands: /help, /link, /new, /reset, /stop, /steer, /models, and /model.

Also exposes ``parse_link_command`` so the Socket Mode gateway can intercept
plain-text ``/link`` / ``link`` messages (when the slash command is not
registered in the Slack app console, or the user types it as a DM).
"""

from __future__ import annotations

import re
from typing import Any

from loguru import logger

from cubeplex.im.link import resolve_mailto_email

# Slack auto-linkifies bare emails as ``<mailto:user@host|user@host>``
# (or just ``<mailto:user@host>``). Unwrap before matching so typed and
# auto-linked forms both work.
_SLACK_MAILTO_RE = re.compile(
    r"<mailto:([^|>]+)(?:\|([^>]*))?>",
    re.IGNORECASE,
)
_LINK_RE = re.compile(
    r"^\s*/?link\s+(\S+@\S+\.\S+)\s*$",
    re.IGNORECASE,
)


def _unwrap_slack_mailto(text: str) -> str:
    return _SLACK_MAILTO_RE.sub(
        lambda m: resolve_mailto_email(m.group(2), m.group(1)),
        text,
    )


def parse_link_command(text: str) -> str | None:
    """Extract email from a /link or link text command. None if not a match."""
    m = _LINK_RE.match(_unwrap_slack_mailto(text or ""))
    return m.group(1).strip().lower() if m else None


def register_commands(
    app: Any,
    *,
    account_id: str,
    workspace_id: str,
    session_maker: Any,
    run_manager: Any = None,
) -> None:
    """Register /help, /link, /new, /reset, /stop, /steer, /models, and /model slash commands."""

    @app.command("/help")  # type: ignore[untyped-decorator]
    async def cmd_help(ack: Any, command: dict[str, Any], respond: Any) -> None:
        await ack()
        await _handle_control_slash(
            command,
            respond,
            account_id=account_id,
            session_maker=session_maker,
            run_manager=run_manager,
            text="/help",
            failure_reply="Failed to show help.",
        )

    @app.command("/link")  # type: ignore[untyped-decorator]
    async def cmd_link(ack: Any, command: dict[str, Any], respond: Any) -> None:
        await ack()
        raw_text = (command.get("text") or "").strip()
        email = parse_link_command(f"/link {raw_text}") if raw_text else None
        if not email:
            # Slash-command text is only the argument; accept bare email too.
            email = _unwrap_slack_mailto(raw_text).strip().lower() if raw_text else ""
            if not email or "@" not in email:
                await respond(
                    "Usage: `/link you@example.com`",
                    response_type="ephemeral",
                )
                return

        sender_ref = command.get("user_id", "")
        if not sender_ref:
            await respond(
                "Could not determine your user ID.",
                response_type="ephemeral",
            )
            return

        try:
            from cubeplex.im.link import get_frontend_base_url, get_jwt_secret, sign_link_token

            token = sign_link_token(
                im_user_id=sender_ref,
                email=email,
                account_id=account_id,
                workspace_id=workspace_id,
                platform="slack",
                secret=get_jwt_secret(),
            )
        except Exception:
            logger.opt(exception=True).warning("[Slack] sign_link_token failed")
            await respond("Failed to generate link.", response_type="ephemeral")
            return

        base = get_frontend_base_url()
        url = f"{base}/im-link?token={token}"
        await respond(
            f"Click to complete linking:\n{url}",
            response_type="ephemeral",
        )

    @app.command("/new")  # type: ignore[untyped-decorator]
    async def cmd_new(ack: Any, command: dict[str, Any], respond: Any) -> None:
        await ack()
        await _handle_reset_slash(
            command, respond, account_id=account_id, session_maker=session_maker
        )

    @app.command("/reset")  # type: ignore[untyped-decorator]
    async def cmd_reset(ack: Any, command: dict[str, Any], respond: Any) -> None:
        await ack()
        await _handle_reset_slash(
            command, respond, account_id=account_id, session_maker=session_maker
        )

    @app.command("/stop")  # type: ignore[untyped-decorator]
    async def cmd_stop(ack: Any, command: dict[str, Any], respond: Any) -> None:
        await ack()
        await _handle_stop_slash(
            command,
            respond,
            account_id=account_id,
            session_maker=session_maker,
            run_manager=run_manager,
        )

    @app.command("/steer")  # type: ignore[untyped-decorator]
    async def cmd_steer(ack: Any, command: dict[str, Any], respond: Any) -> None:
        await ack()
        await _handle_steer_slash(
            command,
            respond,
            account_id=account_id,
            session_maker=session_maker,
            run_manager=run_manager,
        )

    @app.command("/models")  # type: ignore[untyped-decorator]
    async def cmd_models(ack: Any, command: dict[str, Any], respond: Any) -> None:
        await ack()
        await _handle_control_slash(
            command,
            respond,
            account_id=account_id,
            session_maker=session_maker,
            run_manager=run_manager,
            text="/models",
            failure_reply="Failed to list models.",
        )

    @app.command("/model")  # type: ignore[untyped-decorator]
    async def cmd_model(ack: Any, command: dict[str, Any], respond: Any) -> None:
        await ack()
        arg = (command.get("text") or "").strip()
        await _handle_control_slash(
            command,
            respond,
            account_id=account_id,
            session_maker=session_maker,
            run_manager=run_manager,
            text=f"/model {arg}" if arg else "/model",
            failure_reply="Failed to switch model.",
        )


async def _handle_reset_slash(
    command: dict[str, Any],
    respond: Any,
    *,
    account_id: str,
    session_maker: Any,
) -> None:
    """Rotate the IM conversation for the slash-command's channel/scope."""
    from cubeplex.im.reset_command import apply_reset_command, format_reset_reply
    from cubeplex.im.types import (
        DM_SCOPE_KEY,
        lookup_binding_mode,
        make_channel_scope,
        make_participant_scope,
    )

    channel_id = command.get("channel_id") or ""
    user_id = command.get("user_id") or ""
    if not channel_id or not user_id:
        await respond("Could not determine channel or user.", response_type="ephemeral")
        return

    # Slack DM channel IDs start with ``D``.
    if channel_id.startswith("D"):
        scope_key = DM_SCOPE_KEY
    else:
        binding_mode = await lookup_binding_mode(session_maker, account_id, channel_id)
        scope_key = (
            make_channel_scope() if binding_mode == "shared" else make_participant_scope(user_id)
        )

    try:
        outcome = await apply_reset_command(
            session_maker=session_maker,
            account_id=account_id,
            channel_id=channel_id,
            scope_key=scope_key,
        )
    except Exception:
        logger.opt(exception=True).warning("[Slack] /new slash handler failed")
        await respond("Failed to start a new conversation.", response_type="ephemeral")
        return

    await respond(format_reset_reply(outcome), response_type="ephemeral")


async def _resolve_slack_slash_scope(
    command: dict[str, Any],
    *,
    account_id: str,
    session_maker: Any,
) -> tuple[str, str, str, str] | None:
    from cubeplex.im.types import (
        DM_SCOPE_KEY,
        lookup_binding_mode,
        make_channel_scope,
        make_participant_scope,
    )

    channel_id = command.get("channel_id") or ""
    user_id = command.get("user_id") or ""
    if not channel_id or not user_id:
        return None
    if channel_id.startswith("D"):
        return channel_id, user_id, DM_SCOPE_KEY, "dm"
    binding_mode = await lookup_binding_mode(session_maker, account_id, channel_id)
    scope_key = (
        make_channel_scope() if binding_mode == "shared" else make_participant_scope(user_id)
    )
    return channel_id, user_id, scope_key, "channel"


async def _handle_control_slash(
    command: dict[str, Any],
    respond: Any,
    *,
    account_id: str,
    session_maker: Any,
    run_manager: Any,
    text: str,
    failure_reply: str,
) -> None:
    from uuid import uuid4

    from cubeplex.im.control_commands import maybe_handle_control_command
    from cubeplex.im.types import InboundEvent
    from cubeplex.models.im_connector import IMConnectorAccount

    resolved = await _resolve_slack_slash_scope(
        command, account_id=account_id, session_maker=session_maker
    )
    if resolved is None:
        await respond("Could not determine channel or user.", response_type="ephemeral")
        return
    channel_id, user_id, scope_key, scope_kind = resolved
    trigger_id = command.get("trigger_id") or uuid4().hex
    event = InboundEvent(
        platform="slack",
        platform_event_id=f"slash:{trigger_id}",
        account_external_id="",
        channel_id=channel_id,
        scope_key=scope_key,
        scope_kind=scope_kind,
        sender_ref=user_id,
        sender_open_id=user_id,
        text=text,
        reply_to_id=None,
        inbound_message_id="",
    )
    async with session_maker() as session:
        account = await session.get(IMConnectorAccount, account_id)
    if account is None:
        await respond("IM account not found.", response_type="ephemeral")
        return
    event.account_external_id = account.external_account_id

    async def _reply(reply_text: str) -> None:
        await respond(reply_text, response_type="ephemeral")

    try:
        await maybe_handle_control_command(
            event,
            account=account,
            session_maker=session_maker,
            run_manager=run_manager,
            reply=_reply,
            require_linked_member=True,
        )
    except Exception:
        logger.opt(exception=True).warning("[Slack] {} slash handler failed", text.split()[0])
        await respond(failure_reply, response_type="ephemeral")


async def _handle_stop_slash(
    command: dict[str, Any],
    respond: Any,
    *,
    account_id: str,
    session_maker: Any,
    run_manager: Any,
) -> None:
    await _handle_control_slash(
        command,
        respond,
        account_id=account_id,
        session_maker=session_maker,
        run_manager=run_manager,
        text="/stop",
        failure_reply="Failed to stop current reply.",
    )


async def _handle_steer_slash(
    command: dict[str, Any],
    respond: Any,
    *,
    account_id: str,
    session_maker: Any,
    run_manager: Any,
) -> None:
    from uuid import uuid4

    from cubeplex.im.control_commands import (
        STEER_IDLE_FALLBACK_SLASH_REPLY,
        STEER_USAGE_REPLY,
        EphemeralRejectionNotifier,
        maybe_handle_control_command,
    )
    from cubeplex.im.identity import NullIdentityResolver
    from cubeplex.im.inbound import ingest_inbound_event
    from cubeplex.im.types import InboundEvent
    from cubeplex.models.im_connector import IMConnectorAccount

    resolved = await _resolve_slack_slash_scope(
        command, account_id=account_id, session_maker=session_maker
    )
    if resolved is None:
        await respond("Could not determine channel or user.", response_type="ephemeral")
        return
    channel_id, user_id, scope_key, scope_kind = resolved
    content = (command.get("text") or "").strip()
    if not content:
        await respond(STEER_USAGE_REPLY, response_type="ephemeral")
        return

    trigger_id = command.get("trigger_id") or uuid4().hex
    event = InboundEvent(
        platform="slack",
        platform_event_id=f"slash:{trigger_id}",
        account_external_id="",
        channel_id=channel_id,
        scope_key=scope_key,
        scope_kind=scope_kind,
        sender_ref=user_id,
        sender_open_id=user_id,
        text=f"/steer {content}",
        reply_to_id=None,
        inbound_message_id="",
    )
    async with session_maker() as session:
        account = await session.get(IMConnectorAccount, account_id)
    if account is None:
        await respond("IM account not found.", response_type="ephemeral")
        return
    event.account_external_id = account.external_account_id

    async def _reply(text: str) -> None:
        await respond(text, response_type="ephemeral")

    try:
        handled = await maybe_handle_control_command(
            event,
            account=account,
            session_maker=session_maker,
            run_manager=run_manager,
            reply=_reply,
            require_linked_member=True,
        )
        if not handled:
            ingest_res = await ingest_inbound_event(
                event,
                account=account,
                session_maker=session_maker,
                identity_resolver=NullIdentityResolver(),
                rejection_notifier=EphemeralRejectionNotifier(_reply),
            )
            if ingest_res.outcome == "enqueued":
                await respond(STEER_IDLE_FALLBACK_SLASH_REPLY, response_type="ephemeral")
    except Exception:
        logger.opt(exception=True).warning("[Slack] /steer slash handler failed")
        await respond("Failed to process /steer.", response_type="ephemeral")
