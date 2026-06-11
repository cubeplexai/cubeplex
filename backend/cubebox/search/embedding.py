"""OpenAI-protocol embedding HTTP client.

Configured to talk to DashScope, OpenAI, or any local /v1-compatible server.
The model_id field encodes the (model, host) pair so chunks can be
selectively reindexed when either changes.
"""

import os
from typing import Any
from urllib.parse import urlparse

import httpx

from cubebox.config import config


class EmbeddingProvider:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        dimensions: int,
        timeout_seconds: int,
        batch_size: int = 32,
        _transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self.dimensions = dimensions
        self._timeout = timeout_seconds
        self._batch_size = batch_size
        # One AsyncClient per provider instance — connection pool is reused
        # across embed calls; lifetime tied to app lifespan via aclose().
        client_kwargs: dict[str, Any] = {
            "base_url": self._base_url,
            "timeout": timeout_seconds,
            "headers": {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        }
        if _transport is not None:
            client_kwargs["transport"] = _transport
        self._client = httpx.AsyncClient(**client_kwargs)

    @classmethod
    def from_config(cls) -> "EmbeddingProvider":
        api_key_env = config.get("search.embedding.api_key_env", "DASHSCOPE_API_KEY")
        api_key = os.environ.get(api_key_env, "")
        if not api_key:
            raise RuntimeError(
                f"embedding api key not set; export ${api_key_env} or disable search.enabled"
            )
        return cls(
            base_url=config.get(
                "search.embedding.base_url",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
            ),
            api_key=api_key,
            model=config.get("search.embedding.model", "qwen3-embedding-0.6b"),
            dimensions=int(config.get("search.embedding.dimensions", 1024)),
            timeout_seconds=int(config.get("search.embedding.timeout_seconds", 30)),
            batch_size=int(config.get("search.embedding.batch_size", 32)),
        )

    @property
    def model_id(self) -> str:
        host = urlparse(self._base_url).hostname or "unknown"
        return f"{self._model}@{host}"

    async def aclose(self) -> None:
        await self._client.aclose()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        out: list[list[float]] = []
        for i in range(0, len(texts), self._batch_size):
            batch = texts[i : i + self._batch_size]
            out.extend(await self._embed_batch(batch))
        return out

    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        body = {"model": self._model, "input": texts}
        resp = await self._client.post("/embeddings", json=body)
        resp.raise_for_status()
        data = resp.json()
        items = sorted(data["data"], key=lambda d: d["index"])
        return [list(d["embedding"]) for d in items]
