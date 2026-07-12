"""Unit tests for the egress exchange mTLS listener helpers.

The full mTLS handshake is exercised by the real-cluster E2E; here we verify the
two pure-ish seams: the custom protocol injects the TLS transport into the ASGI
scope (so the app can read the peer cert), and build_exchange_app wires the
authenticator + encryption backend onto a minimal app that mounts only the
exchange route.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from cubeplex.sandbox_env.exchange_auth import MtlsAuthenticator
from cubeplex.sandbox_env.exchange_listener import (
    PeercertHttpToolsProtocol,
    build_exchange_app,
)


def test_protocol_injects_transport_into_scope(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """on_message_begin must place self.transport into the scope so
    _peercert_from_scope can read the verified client cert. uvicorn does not do
    this on its own."""
    import cubeplex.sandbox_env.exchange_listener as mod

    proto = PeercertHttpToolsProtocol.__new__(PeercertHttpToolsProtocol)
    sentinel_transport = object()
    proto.transport = sentinel_transport  # type: ignore[assignment]

    # The base on_message_begin is what creates self.scope on a live server; stub
    # it to just set an empty scope so we don't need a real uvicorn connection.
    def _base_begin(self: object) -> None:
        proto.scope = {"type": "http"}  # type: ignore[attr-defined]

    monkeypatch.setattr(mod.HttpToolsProtocol, "on_message_begin", _base_begin)

    proto.on_message_begin()

    assert proto.scope["transport"] is sentinel_transport


def test_build_exchange_app_mounts_only_exchange_route() -> None:
    backend = MagicMock()
    auth = MtlsAuthenticator()
    app = build_exchange_app(encryption_backend=backend, authenticator=auth)

    assert app.state.encryption_backend is backend
    assert app.state.sidecar_authenticator is auth

    paths = {route.path for route in app.routes}  # type: ignore[attr-defined]
    assert "/api/v1/internal/egress/exchange" in paths
    # The public app's routes must NOT be present — this is a minimal app.
    assert not any(p.startswith("/api/v1/ws/") for p in paths)
