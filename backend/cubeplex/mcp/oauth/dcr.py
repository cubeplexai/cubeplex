"""RFC 7591 Dynamic Client Registration (DCR) for MCP OAuth.

The ``DCRClient`` posts a registration request to the AS-supplied
``registration_endpoint`` and returns the parsed ``DCRResponse``. Errors
(both authoritative ``error`` payloads and unexpected statuses) raise
``DCRError``. Network errors propagate as ``httpx.HTTPError``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from cubeplex.mcp.exceptions import DCRError

DEFAULT_GRANT_TYPES: list[str] = ["authorization_code", "refresh_token"]
DEFAULT_RESPONSE_TYPES: list[str] = ["code"]
DEFAULT_TOKEN_AUTH_METHOD: str = "none"


@dataclass(frozen=True)
class DCRRequest:
    """Outgoing RFC 7591 dynamic client registration request body."""

    redirect_uris: list[str]
    client_name: str
    grant_types: list[str] = field(default_factory=lambda: list(DEFAULT_GRANT_TYPES))
    response_types: list[str] = field(default_factory=lambda: list(DEFAULT_RESPONSE_TYPES))
    token_endpoint_auth_method: str = DEFAULT_TOKEN_AUTH_METHOD
    scope: str | None = None

    def to_json(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "redirect_uris": list(self.redirect_uris),
            "client_name": self.client_name,
            "grant_types": list(self.grant_types),
            "response_types": list(self.response_types),
            "token_endpoint_auth_method": self.token_endpoint_auth_method,
        }
        if self.scope is not None:
            body["scope"] = self.scope
        return body


@dataclass(frozen=True)
class DCRResponse:
    """Parsed RFC 7591 dynamic client registration response."""

    client_id: str
    client_secret: str | None
    client_id_issued_at: int | None
    client_secret_expires_at: int | None
    raw: dict[str, Any]


class DCRClient:
    """Thin wrapper around an ``httpx.AsyncClient`` for RFC 7591 DCR."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    async def register(
        self,
        registration_endpoint: str,
        request: DCRRequest,
    ) -> DCRResponse:
        response = await self._http.post(
            registration_endpoint,
            json=request.to_json(),
            headers={"Accept": "application/json"},
        )
        if response.status_code not in (200, 201):
            error: str | None = None
            error_description: str | None = None
            try:
                body = response.json()
                if isinstance(body, dict):
                    error = _opt_str(body.get("error"))
                    error_description = _opt_str(body.get("error_description"))
            except ValueError:
                pass
            raise DCRError(
                status=response.status_code,
                error=error,
                error_description=error_description,
            )
        body = response.json()
        if not isinstance(body, dict) or "client_id" not in body:
            raise DCRError(
                status=response.status_code,
                error="invalid_response",
                error_description="DCR response missing client_id",
            )
        return DCRResponse(
            client_id=str(body["client_id"]),
            client_secret=_opt_str(body.get("client_secret")),
            client_id_issued_at=_opt_int(body.get("client_id_issued_at")),
            client_secret_expires_at=_opt_int(body.get("client_secret_expires_at")),
            raw=dict(body),
        )


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _opt_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
