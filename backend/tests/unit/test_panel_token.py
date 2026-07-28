"""Token sign + verify for sandbox panel (browser/terminal) proxy access."""

from __future__ import annotations

import base64
import json
import time
from datetime import timedelta

import pytest

from cubeplex.sandbox.panel_token import (
    sign_panel_token,
    verify_panel_token,
)

_SECRET = "test-secret-for-panel-tokens"


def _b64url_decode(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


class TestPanelToken:
    def test_roundtrip(self) -> None:
        token = sign_panel_token(
            sandbox_id="sbx_abc",
            port=8080,
            secret=_SECRET,
            ttl=timedelta(hours=1),
        )
        claims = verify_panel_token(token, secret=_SECRET)
        assert claims.sandbox_id == "sbx_abc"
        assert claims.port == 8080
        assert isinstance(claims.port, int)

    def test_bad_signature_rejected(self) -> None:
        token = sign_panel_token(
            sandbox_id="sbx_1", port=7681, secret=_SECRET, ttl=timedelta(hours=1)
        )
        with pytest.raises(ValueError, match="Invalid or expired"):
            verify_panel_token(token, secret="wrong-secret")

    def test_wrong_issuer_rejected(self) -> None:
        import jwt

        token = jwt.encode(
            {"sid": "sbx_1", "port": 8080, "iss": "other", "exp": int(time.time()) + 600},
            _SECRET,
            algorithm="HS256",
        )
        with pytest.raises(ValueError, match="Invalid or expired"):
            verify_panel_token(token, secret=_SECRET)

    def test_expired_token_rejected(self) -> None:
        token = sign_panel_token(
            sandbox_id="sbx_1", port=8080, secret=_SECRET, ttl=timedelta(seconds=-10)
        )
        with pytest.raises(ValueError, match="Invalid or expired"):
            verify_panel_token(token, secret=_SECRET)

    def test_tampered_port_rejected(self) -> None:
        # A token whose payload is edited without re-signing must fail.
        token = sign_panel_token(
            sandbox_id="sbx_1", port=8080, secret=_SECRET, ttl=timedelta(hours=1)
        )
        header, payload, sig = token.split(".")
        # Re-point the token at a different port, keeping the original signature.
        # Editing the base64 text directly is not safe here: a 32-byte HMAC encodes
        # to 43 base64 chars, whose last char carries only 4 significant bits, so
        # four different characters decode to identical bytes and "flipping" one of
        # them is silently a no-op.
        claims = json.loads(_b64url_decode(payload))
        assert claims["port"] == 8080
        claims["port"] = 9090
        bad_payload = _b64url_encode(json.dumps(claims, separators=(",", ":")).encode())
        assert bad_payload != payload
        with pytest.raises(ValueError, match="Invalid or expired"):
            verify_panel_token(f"{header}.{bad_payload}.{sig}", secret=_SECRET)
