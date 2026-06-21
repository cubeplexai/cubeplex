"""Slack slash commands: /link."""

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
    """Register /link slash command on the Slack app."""

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
