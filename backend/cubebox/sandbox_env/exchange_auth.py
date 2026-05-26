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


class MtlsAuthenticator:
    """Production. Reads the verified client cert; sandbox_id is its CN/SAN.

    The TLS layer (uvicorn ssl_cert_reqs=CERT_REQUIRED + ssl_ca_certs=<our CA>)
    has already verified the chain; here we just extract sandbox_id from the
    peer certificate surfaced on the request.
    """

    def __init__(self, *, sandbox_id_field: str = "CN") -> None:
        self._field = sandbox_id_field

    async def verify(self, request: Any) -> SidecarIdentity:
        cert = getattr(request, "client_cert", None)
        if not cert:
            raise PermissionError("no client certificate")
        sandbox_id = cert.get(self._field) if isinstance(cert, dict) else None
        if not sandbox_id:
            raise PermissionError(f"client cert missing {self._field}")
        return SidecarIdentity(sandbox_id=sandbox_id)


def build_sidecar_authenticator(
    config: dict[str, Any], *, deployment_mode: str
) -> SidecarAuthenticator:
    mode = config.get("mode", "mtls")
    if mode == "dev":
        if deployment_mode == "production":
            raise RuntimeError(
                "egress exchange dev authenticator selected in production deployment mode"
            )
        token = config.get("dev_token")
        if not token:
            raise RuntimeError("dev authenticator requires dev_token")
        return DevSharedSecretAuthenticator(token=token)
    if mode == "mtls":
        return MtlsAuthenticator(sandbox_id_field=config.get("sandbox_id_field", "CN"))
    raise RuntimeError(f"unknown egress exchange auth mode: {mode!r}")
