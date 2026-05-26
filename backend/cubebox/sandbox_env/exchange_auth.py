"""Sidecar identity verification for the egress exchange endpoint.

Pluggable so the same endpoint works in production (mTLS, per-sandbox client
cert carrying sandbox_id) and bare-local dev (shared secret + explicit
sandbox_id header). The dev backend must never run in production.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class SidecarIdentity:
    sandbox_id: str


class SidecarAuthenticator(Protocol):
    async def verify(self, request: Any) -> SidecarIdentity:
        """Return the verified sidecar identity, or raise PermissionError."""
        ...


class DevSharedSecretAuthenticator:
    """Bare-local only. Trusts a shared token; takes sandbox_id from a header."""

    def __init__(self, *, token: str) -> None:
        self._token = token

    async def verify(self, request: Any) -> SidecarIdentity:
        if request.headers.get("x-egress-dev-token") != self._token:
            raise PermissionError("bad dev token")
        sandbox_id = request.headers.get("x-egress-sandbox-id")
        if not sandbox_id:
            raise PermissionError("missing x-egress-sandbox-id")
        return SidecarIdentity(sandbox_id=sandbox_id)


def _peercert_from_scope(scope: dict[str, Any]) -> dict[str, Any] | None:
    """Extract the verified peer certificate dict from an ASGI scope.

    Precedence (highest to lowest):
    1. ``scope["transport"].get_extra_info("peercert")`` — uvicorn with
       ``ssl_cert_reqs=CERT_REQUIRED`` populates this on the underlying asyncio
       SSL transport.  Returns the standard Python ssl ``getpeercert()`` dict,
       e.g. ``{"subject": ((("commonName", "sbx-1"),),), ...}``.
    2. ``scope["extensions"]["tls"]["peercert"]`` — future / alternative ASGI
       server convention; included defensively.

    Chain validation is the TLS layer's responsibility (``CERT_REQUIRED`` +
    ``ssl_ca_certs=<egress CA>``).  This function only surfaces the identity.
    """
    # Path 1: uvicorn / asyncio SSL transport
    transport = scope.get("transport")
    if transport is not None:
        try:
            cert = transport.get_extra_info("peercert")
            if isinstance(cert, dict) and cert:
                return cert
        except Exception:  # noqa: BLE001 — defensive; non-SSL transports may raise
            pass

    # Path 2: ASGI extensions dict (less common; included for forward compat)
    extensions = scope.get("extensions") or {}
    tls_info = extensions.get("tls") if isinstance(extensions, dict) else None
    if isinstance(tls_info, dict):
        cert = tls_info.get("peercert")
        if isinstance(cert, dict) and cert:
            return cert

    return None


def _sandbox_id_from_peercert(peercert: dict[str, Any]) -> str | None:
    """Parse a standard Python ssl.getpeercert() dict and return the CN, or None.

    The dict has the shape::

        {"subject": ((("commonName", "sbx-1"),), (("organizationName", "x"),)), ...}

    Each element of ``subject`` is a tuple of (key, value) pairs (RFC 4514
    multi-value RDNs are rare but legal, hence the double nesting).
    """
    for rdn in peercert.get("subject", ()):
        for attr_type, attr_value in rdn:
            if attr_type == "commonName":
                return str(attr_value) if attr_value else None
    return None


class MtlsAuthenticator:
    """Production. Derives the verified sidecar identity (CN = sandbox_id) from
    the client certificate the egress sidecar presents.

    The exchange endpoint is served by :class:`ExchangeListener`, a dedicated
    uvicorn listener that terminates mTLS (``CERT_REQUIRED`` against the egress
    CA). The identity is therefore cryptographically bound to a per-sandbox
    client cert — there is no forgeable header to trust, and no proxy in the
    trust path. Chain validation is the TLS layer's job; this code only extracts
    the already-verified CN.

    Lookup precedence:

    1. ``request.client_cert`` dict — lets unit/integration tests inject a
       synthetic peercert without a live TLS socket.
    2. ASGI scope peercert — populated by ``PeercertHttpToolsProtocol`` which
       injects the TLS transport into the scope (uvicorn does not do this on its
       own).

    Raises ``PermissionError`` if no verified identity can be derived.
    """

    async def verify(self, request: Any) -> SidecarIdentity:
        # Path 1: explicit dict attribute (unit tests, integration mocks).
        explicit = getattr(request, "client_cert", None)
        if isinstance(explicit, dict) and explicit:
            peercert: dict[str, Any] = explicit
        else:
            # Path 2: real ASGI scope transport peercert.
            scope: dict[str, Any] = getattr(request, "scope", {})
            found = _peercert_from_scope(scope)
            if found is None:
                raise PermissionError("no verified client identity")
            peercert = found

        sandbox_id = _sandbox_id_from_peercert(peercert)
        if not sandbox_id:
            raise PermissionError("client cert missing CN")
        return SidecarIdentity(sandbox_id=sandbox_id)


_DEV_ALLOWED_ENVS = {"development", "testing", "test"}


def build_sidecar_authenticator(config: dict[str, Any], *, env: str) -> SidecarAuthenticator:
    mode = config.get("mode", "mtls")
    if mode == "dev":
        if env.lower() not in _DEV_ALLOWED_ENVS:
            raise RuntimeError(
                f"egress exchange dev authenticator is not allowed in env={env!r}; "
                f"only {sorted(_DEV_ALLOWED_ENVS)} are permitted"
            )
        token = config.get("dev_token")
        if not token:
            raise RuntimeError("dev authenticator requires dev_token")
        return DevSharedSecretAuthenticator(token=token)
    if mode == "mtls":
        return MtlsAuthenticator()
    raise RuntimeError(f"unknown egress exchange auth mode: {mode!r}")
