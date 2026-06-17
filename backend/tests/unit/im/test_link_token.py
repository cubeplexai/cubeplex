"""Token sign + verify for IM identity linking."""

from __future__ import annotations

import time

import pytest

from cubebox.im.link import sign_link_token, verify_link_token

_SECRET = "test-secret-for-link-tokens"


class TestSignLinkToken:
    def test_roundtrip(self) -> None:
        token = sign_link_token(
            im_user_id="discord_123",
            email="Chris@Example.COM",
            account_id="imca_abc",
            workspace_id="ws_xyz",
            platform="discord",
            secret=_SECRET,
        )
        claims = verify_link_token(token, secret=_SECRET)
        assert claims.im_user_id == "discord_123"
        assert claims.email == "chris@example.com"  # normalized
        assert claims.account_id == "imca_abc"
        assert claims.workspace_id == "ws_xyz"
        assert claims.platform == "discord"

    def test_bad_signature_rejected(self) -> None:
        token = sign_link_token(
            im_user_id="u1",
            email="a@b.com",
            account_id="imca_1",
            workspace_id="ws_1",
            platform="feishu",
            secret=_SECRET,
        )
        with pytest.raises(ValueError, match="Invalid or expired"):
            verify_link_token(token, secret="wrong-secret")

    def test_wrong_issuer_rejected(self) -> None:
        import jwt

        token = jwt.encode(
            {"sub": "u1", "iss": "other", "exp": int(time.time()) + 600},
            _SECRET,
            algorithm="HS256",
        )
        with pytest.raises(ValueError, match="Invalid or expired"):
            verify_link_token(token, secret=_SECRET)

    def test_expired_token_rejected(self) -> None:
        import jwt

        token = jwt.encode(
            {
                "sub": "u1",
                "email": "a@b.com",
                "act": "imca_1",
                "ws": "ws_1",
                "plt": "discord",
                "iss": "cubebox:im-link",
                "exp": int(time.time()) - 10,
                "iat": int(time.time()) - 700,
            },
            _SECRET,
            algorithm="HS256",
        )
        with pytest.raises(ValueError, match="Invalid or expired"):
            verify_link_token(token, secret=_SECRET)
