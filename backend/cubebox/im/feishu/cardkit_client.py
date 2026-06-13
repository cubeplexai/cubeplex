"""HTTP wrapper for Feishu CardKit endpoints.

We hit the CardKit REST API directly because the `lark_oapi` Python SDK
predates the CardKit endpoints. The token provider returns a fresh
tenant_access_token; the wrapper handles retries, throttling buckets,
and idempotent finalize.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import httpx
from loguru import logger

from cubebox.im.outbound import _FloodSignal

_BASE_URL = "https://open.feishu.cn"
_CREATE_RETRY_DELAYS = (0.2, 1.0, 3.0)
# CardKit rate-limit response code (same as IM patch rate limit).
_FLOOD_CODE = 230020


class CardKitError(Exception):
    """Base error for CardKit client failures."""


class CardKitCreateError(CardKitError):
    """create_entity exhausted retries."""


class CardKitRateLimit(_FloodSignal):
    """CardKit returned the 230020 throttle response."""


class CardKitClient:
    """Async CardKit REST client.

    Construction takes a token_provider (sync) returning a fresh
    tenant_access_token, and optionally a transport (for tests) or a
    base_url override (for Lark international domain).
    """

    def __init__(
        self,
        *,
        token_provider: Callable[[], str],
        base_url: str = _BASE_URL,
        transport: httpx.AsyncBaseTransport | httpx.MockTransport | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._token_provider = token_provider
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._transport = transport

    def _new_client(self) -> httpx.AsyncClient:
        kwargs: dict[str, Any] = {"timeout": self._timeout}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.AsyncClient(**kwargs)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token_provider()}",
            "Content-Type": "application/json; charset=utf-8",
        }

    async def create_entity(self, card_json: dict[str, Any]) -> str:
        """POST /open-apis/cardkit/v1/cards. Returns the new card_id.

        Retries on 5xx / network errors with exponential backoff.
        Raises ``CardKitCreateError`` after exhausting retries.
        """
        url = f"{self._base_url}/open-apis/cardkit/v1/cards"
        payload = {"type": "card_json", "data": card_json}
        last_exc: Exception | None = None
        async with self._new_client() as http:
            for attempt in range(len(_CREATE_RETRY_DELAYS) + 1):
                try:
                    resp = await http.post(url, json=payload, headers=self._headers())
                    if 500 <= resp.status_code < 600:
                        raise CardKitError(f"create_entity HTTP {resp.status_code}")
                    body = resp.json()
                    code = int(body.get("code", -1))
                    if code == 0:
                        data = body.get("data") or {}
                        card_id = str(data.get("card_id") or "")
                        if not card_id:
                            raise CardKitCreateError("create_entity returned no card_id")
                        return card_id
                    raise CardKitError(f"create_entity code={code} msg={body.get('msg')}")
                except (httpx.HTTPError, CardKitError) as exc:
                    last_exc = exc
                    logger.warning(
                        "[CardKit] create_entity attempt {} failed: {}", attempt + 1, exc
                    )
                    if attempt < len(_CREATE_RETRY_DELAYS):
                        await asyncio.sleep(_CREATE_RETRY_DELAYS[attempt])
                        continue
                    break
        raise CardKitCreateError(str(last_exc) if last_exc else "unknown")


__all__ = [
    "CardKitClient",
    "CardKitCreateError",
    "CardKitError",
    "CardKitRateLimit",
]
