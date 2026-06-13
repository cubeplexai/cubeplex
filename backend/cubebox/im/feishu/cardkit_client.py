"""HTTP wrapper for Feishu CardKit endpoints.

We hit the CardKit REST API directly because the `lark_oapi` Python SDK
predates the CardKit endpoints. The token provider returns a fresh
tenant_access_token; the wrapper handles retries, throttling buckets,
and idempotent finalize.
"""

from __future__ import annotations

import asyncio
import json as _json
from collections.abc import Callable
from typing import Any

import httpx
from loguru import logger

from cubebox.im.outbound import _FloodSignal

_BASE_URL = "https://open.feishu.cn"
_CREATE_RETRY_DELAYS = (0.2, 1.0, 3.0)
_FINALIZE_RETRY_DELAYS = (0.2, 0.5, 1.0, 3.0, 10.0, 30.0, 30.0, 30.0, 30.0)
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
        # One AsyncClient per CardKitClient — reused across all CardKit ops
        # in a single run (create + N stream_text + M patch_card + finalize
        # against open.feishu.cn). Without this, each call re-opens TCP +
        # TLS to Feishu, which adds 50-200ms per token on a cold path.
        # Owner closes via ``aclose()``; the tailer's run() finally block
        # is the canonical close site.
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            kwargs: dict[str, Any] = {"timeout": self._timeout}
            if self._transport is not None:
                kwargs["transport"] = self._transport
            self._client = httpx.AsyncClient(**kwargs)
        return self._client

    async def aclose(self) -> None:
        """Release the underlying connection pool. Idempotent."""
        if self._client is not None:
            try:
                await self._client.aclose()
            finally:
                self._client = None

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
        # Feishu's CardKit `data` field is a JSON-encoded STRING, not a nested
        # object — sending the dict gets `code=9499 Invalid parameter type in
        # json: Data` back. Same encoding on every POST/PATCH below.
        payload = {"type": "card_json", "data": _json.dumps(card_json, ensure_ascii=False)}
        last_exc: Exception | None = None
        http = self._get_client()
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
                logger.warning("[CardKit] create_entity attempt {} failed: {}", attempt + 1, exc)
                if attempt < len(_CREATE_RETRY_DELAYS):
                    await asyncio.sleep(_CREATE_RETRY_DELAYS[attempt])
                    continue
                break
        raise CardKitCreateError(str(last_exc) if last_exc else "unknown")

    async def stream_text(
        self,
        *,
        card_id: str,
        element_id: str,
        content: str,
        sequence: int,
    ) -> None:
        """PUT /open-apis/cardkit/v1/cards/{card_id}/elements/{element_id}/content.

        Pushes an incremental text update to a streaming element. Raises
        ``CardKitRateLimit`` on code 230020 so the caller can skip-merge
        into the next stream attempt without counting it against retry.
        """
        url = f"{self._base_url}/open-apis/cardkit/v1/cards/{card_id}/elements/{element_id}/content"
        payload = {
            "content": content,
            "sequence": sequence,
            "uuid": f"{card_id}-{sequence}",
        }
        http = self._get_client()
        resp = await http.put(url, json=payload, headers=self._headers())
        body = resp.json()
        code = int(body.get("code", -1))
        if code == _FLOOD_CODE:
            raise CardKitRateLimit(f"stream_text flood (code={code})")
        if code != 0:
            raise CardKitError(f"stream_text code={code} msg={body.get('msg')}")

    async def patch_card(
        self,
        *,
        card_id: str,
        card_json: dict[str, Any],
        sequence: int,
    ) -> None:
        """PUT /open-apis/cardkit/v1/cards/{card_id}.

        Replaces the whole card JSON. The Feishu CardKit "update card"
        endpoint is PUT, not PATCH — PATCH on the same path targets
        ``/settings`` only. Raises ``CardKitRateLimit`` on 230020; caller
        coalesces.
        """
        url = f"{self._base_url}/open-apis/cardkit/v1/cards/{card_id}"
        payload = {
            "card": {"type": "card_json", "data": _json.dumps(card_json, ensure_ascii=False)},
            "uuid": f"{card_id}-{sequence}",
            "sequence": sequence,
        }
        http = self._get_client()
        resp = await http.put(url, json=payload, headers=self._headers())
        body = resp.json()
        code = int(body.get("code", -1))
        if code == _FLOOD_CODE:
            raise CardKitRateLimit(f"patch_card flood (code={code})")
        if code != 0:
            raise CardKitError(f"patch_card code={code} msg={body.get('msg')}")

    async def finalize(
        self,
        *,
        card_id: str,
        card_json: dict[str, Any],
        sequence: int,
    ) -> bool:
        """Terminal full-card replace via PUT /cards/{card_id}. Idempotent,
        retried up to ~2.5 minutes total.

        Returns True if the final patch landed; False if all retries
        failed (caller logs + accepts half-locked state, sets ❌ reaction).
        """
        url = f"{self._base_url}/open-apis/cardkit/v1/cards/{card_id}"
        payload = {
            "card": {"type": "card_json", "data": _json.dumps(card_json, ensure_ascii=False)},
            "uuid": f"{card_id}-{sequence}",
            "sequence": sequence,
        }
        http = self._get_client()
        for attempt in range(len(_FINALIZE_RETRY_DELAYS) + 1):
            try:
                resp = await http.put(url, json=payload, headers=self._headers())
                if 500 <= resp.status_code < 600:
                    raise CardKitError(f"finalize HTTP {resp.status_code}")
                body = resp.json()
                code = int(body.get("code", -1))
                if code == 0:
                    return True
                if code == _FLOOD_CODE:
                    # Throttle counts as transient; retry like 5xx.
                    raise CardKitError(f"finalize flood (code={code})")
                raise CardKitError(f"finalize code={code} msg={body.get('msg')}")
            except (httpx.HTTPError, CardKitError) as exc:
                logger.warning("[CardKit] finalize attempt {} failed: {}", attempt + 1, exc)
                if attempt < len(_FINALIZE_RETRY_DELAYS):
                    await asyncio.sleep(_FINALIZE_RETRY_DELAYS[attempt])
                    continue
                break
        logger.error("[CardKit] finalize gave up for card_id={}", card_id)
        return False


__all__ = [
    "CardKitClient",
    "CardKitCreateError",
    "CardKitError",
    "CardKitRateLimit",
]
