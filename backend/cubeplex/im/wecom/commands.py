"""WeCom text command parsing and durable response rendering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from cubeplex.im.link import get_frontend_base_url, get_jwt_secret, sign_link_token
from cubeplex.im.reset_command import parse_reset_command
from cubeplex.im.teams.commands import parse_link_command


@dataclass(frozen=True, slots=True)
class WecomCommand:
    kind: Literal["link", "reset"]
    email: str | None = None


def parse_command(text: str) -> WecomCommand | None:
    """Recognize WeCom identity and conversation commands."""
    email = parse_link_command(text)
    if email is not None:
        return WecomCommand(kind="link", email=email)
    if parse_reset_command(text):
        return WecomCommand(kind="reset")
    return None


def render_response(payload: dict[str, Any]) -> str:
    """Render a persisted semantic response, signing link tokens on demand."""
    kind = str(payload.get("kind") or "")
    if kind == "text":
        return str(payload.get("text") or "")
    if kind != "link":
        raise ValueError(f"unsupported WeCom command response kind: {kind or '<empty>'}")
    token = sign_link_token(
        im_user_id=str(payload["im_user_id"]),
        email=str(payload["email"]),
        account_id=str(payload["account_id"]),
        workspace_id=str(payload["workspace_id"]),
        platform=str(payload["platform"]),
        secret=get_jwt_secret(),
        chat_id=str(payload.get("chat_id") or ""),
    )
    url = f"{get_frontend_base_url()}/im-link?token={token}"
    return f"Click to bind your CubePlex account:\n\n[Link your account]({url})"
