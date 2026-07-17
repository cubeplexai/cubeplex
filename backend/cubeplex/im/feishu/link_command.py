"""Feishu /link command parsing + reply.

Both the webhook ingress and the long-connection path must intercept
``/link <email>`` (or its Chinese alias ``绑定 <email>``) BEFORE handing the
message to ``ingest_inbound_event`` — otherwise the identity gate replies
"I don't know who you are" instead of issuing the link URL.
"""

from __future__ import annotations

import re as _re
from typing import Any

from loguru import logger

from cubeplex.models.im_connector import IMConnectorAccount

_LINK_RE = _re.compile(
    r"^\s*(?:/link|绑定)\s+(\S+@\S+\.\S+)\s*$",
    _re.IGNORECASE,
)

# Feishu auto-renders email-like input as `[user@host](mailto:user@host)`.
# Unwrap before regex so the user-typed bare email and the auto-linked
# form both match. Also covers angle-bracketed RFC 5322 form `<email>`.
_MAILTO_AUTOLINK_RE = _re.compile(r"\[[^\]]+\]\(mailto:([^)]+)\)", _re.IGNORECASE)


def _unwrap_email_autolinks(text: str) -> str:
    text = _MAILTO_AUTOLINK_RE.sub(r"\1", text)
    return text.replace("<", "").replace(">", "")


def parse_link_command(text: str) -> str | None:
    """Extract email from a /link or 绑定 command. Returns None if not a match."""
    m = _LINK_RE.match(_unwrap_email_autolinks(text))
    return m.group(1).strip().lower() if m else None


async def handle_link_command(
    *,
    email: str,
    event: Any,
    account: IMConnectorAccount,
    connector: Any,
) -> None:
    """Generate an identity-link token and reply to the Feishu chat."""
    from cubeplex.config import config
    from cubeplex.im.link import sign_link_token

    secret = str(config.get("auth.jwt_secret", "CHANGE_ME"))
    sender_ref = event.sender_ref or event.sender_open_id or ""
    if not sender_ref:
        if connector is not None:
            await connector.send_to_chat(event.channel_id, event.reply_to_id, "无法识别发送者。")
        return

    try:
        token = sign_link_token(
            im_user_id=sender_ref,
            email=email,
            account_id=account.id,
            workspace_id=account.workspace_id,
            platform="feishu",
            secret=secret,
            chat_id=event.channel_id or "",
        )
    except Exception:
        logger.opt(exception=True).warning("[Feishu] sign_link_token failed")
        if connector is not None:
            await connector.send_to_chat(event.channel_id, event.reply_to_id, "生成绑定链接失败。")
        return

    base = str(config.get("frontend_base_url", "http://localhost:3000")).rstrip("/")
    url = f"{base}/im-link?token={token}"
    text = f"点击链接完成绑定：\n{url}"
    if connector is not None:
        await connector.send_to_chat(event.channel_id, event.reply_to_id, text)
    else:
        logger.warning("[Feishu] no connector to reply with link URL")
