from urllib.parse import parse_qs, urlparse

from cubeplex.im.link import verify_link_token
from cubeplex.im.wecom.commands import parse_command, render_response


def test_parse_link_and_reset_commands() -> None:
    link = parse_command(" /link Person@Example.COM ")
    assert link is not None
    assert link.kind == "link"
    assert link.email == "person@example.com"
    assert parse_command("/new").kind == "reset"  # type: ignore[union-attr]
    assert parse_command("新对话").kind == "reset"  # type: ignore[union-attr]
    assert parse_command("hello") is None


def test_render_link_response_signs_saved_claims(monkeypatch) -> None:
    monkeypatch.setattr(
        "cubeplex.im.wecom.commands.get_frontend_base_url",
        lambda: "https://cubeplex.example",
    )
    monkeypatch.setattr("cubeplex.im.wecom.commands.get_jwt_secret", lambda: "test-secret")
    rendered = render_response(
        {
            "kind": "link",
            "im_user_id": "wm-user",
            "email": "person@example.com",
            "account_id": "imac-1",
            "workspace_id": "ws-1",
            "platform": "wecom",
            "chat_id": "chat-1",
        }
    )
    link = rendered.rsplit("(", 1)[1].rstrip(")")
    token = parse_qs(urlparse(link).query)["token"][0]
    claims = verify_link_token(token, secret="test-secret")
    assert claims.email == "person@example.com"
    assert claims.im_user_id == "wm-user"
    assert claims.platform == "wecom"
    assert claims.chat_id == "chat-1"


def test_render_text_response_is_stable() -> None:
    assert render_response({"kind": "text", "text": "✅ done"}) == "✅ done"
