"""Microsoft Graph API client for Teams identity resolution."""

from __future__ import annotations

import time
from typing import Any

import httpx
from loguru import logger

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_TOKEN_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
_GRAPH_SCOPE = "https://graph.microsoft.com/.default"


class TeamsGraphClient:
    """Cached Graph API client for one Teams bot account."""

    def __init__(self, *, app_id: str, app_secret: str, tenant_id: str) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._tenant_id = tenant_id
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    async def _ensure_token(self) -> str:
        if self._token and time.monotonic() < self._token_expires_at - 60:
            return self._token
        url = _TOKEN_URL.format(tenant_id=self._tenant_id)
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._app_id,
                    "client_secret": self._app_secret,
                    "scope": _GRAPH_SCOPE,
                },
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
        self._token = str(data["access_token"])
        self._token_expires_at = time.monotonic() + int(data.get("expires_in", 3600))
        return self._token

    async def get_user_email(self, aad_object_id: str) -> str | None:
        """Resolve AAD Object ID -> email via Graph API GET /users/{id}."""
        try:
            token = await self._ensure_token()
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{_GRAPH_BASE}/users/{aad_object_id}",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"$select": "mail,userPrincipalName"},
                    timeout=10.0,
                )
                if resp.status_code != 200:
                    logger.warning(
                        "[Teams] Graph /users/{} returned {}",
                        aad_object_id,
                        resp.status_code,
                    )
                    return None
                data: dict[str, Any] = resp.json()
                return str(data.get("mail") or data.get("userPrincipalName") or "") or None
        except Exception:
            logger.warning(
                "[Teams] Graph email lookup failed for {}",
                aad_object_id,
                exc_info=True,
            )
            return None
