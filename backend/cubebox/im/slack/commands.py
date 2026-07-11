"""Slack slash commands: /link, /new, /reset."""

from __future__ import annotations

from typing import Any

from loguru import logger


def register_commands(
    app: Any,
    *,
    account_id: str,
    workspace_id: str,
    session_maker: Any,
) -> None:
    """Register /link, /new, and /reset slash commands on the Slack app."""

    @app.command("/link")  # type: ignore[untyped-decorator]
    async def cmd_link(ack: Any, command: dict[str, Any], respond: Any) -> None:
        await ack()
        email = (command.get("text") or "").strip()
        if not email:
            await respond(
                "Usage: `/link your-cubebox-email@example.com`",
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
            from cubebox.im.link import get_frontend_base_url, get_jwt_secret, sign_link_token

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


async def _handle_reset_slash(
    command: dict[str, Any],
    respond: Any,
    *,
    account_id: str,
    session_maker: Any,
) -> None:
    """Rotate the IM conversation for the slash-command's channel/scope."""
    from cubebox.im.reset_command import apply_reset_command, format_reset_reply
    from cubebox.im.types import (
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
