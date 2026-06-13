"""OpenAI-protocol embedding HTTP client.

Configured to talk to OpenAI, DashScope, or any local /v1-compatible server.
The model_id field encodes the (model, host) pair so chunks can be
selectively reindexed when either changes.

Two dim knobs:
  - vector_dim: width of the Postgres `embedding` column. The provider's
    output is checked against this at startup (see startup.py three-way
    check) and at write time (worker writes embedding=NULL on mismatch).
  - api_dimensions: optional `dimensions` parameter sent on each
    /v1/embeddings request. 0 means don't send. Useful for Matryoshka
    models + OpenAI text-embedding-3-* which support truncation server-side.
"""

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
        vector_dim: int,
        api_dimensions: int,
        timeout_seconds: int,
        batch_size: int = 32,
        _transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        # vector_dim is the DB column width and what the rest of the system
        # expects back from .embed(). api_dimensions controls whether we
        # ask the server to truncate; if non-zero it must equal vector_dim.
        self.vector_dim = vector_dim
        self.api_dimensions = api_dimensions
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
        """Build from config. Caller must check `search.embedding.enabled` first.

        Raises RuntimeError when api_key is empty or when api_dimensions is
        non-zero but disagrees with vector_dim — both are operator misconfig.
        """
        api_key = str(config.get("search.embedding.api_key", "") or "")
        if not api_key.strip():
            raise RuntimeError(
                "embedding api key not set; fill search.embedding.api_key or "
                "set CUBEBOX_SEARCH__EMBEDDING__API_KEY"
            )
        vector_dim = int(config.get("search.embedding.vector_dim", 1024))
        api_dimensions = int(config.get("search.embedding.dimensions", 0))
        if api_dimensions and api_dimensions != vector_dim:
            raise RuntimeError(
                f"search.embedding.dimensions ({api_dimensions}) must equal "
                f"vector_dim ({vector_dim}) when non-zero"
            )
        return cls(
            base_url=config.get(
                "search.embedding.base_url",
                "https://api.openai.com/v1",
            ),
            api_key=api_key,
            model=config.get("search.embedding.model", "text-embedding-3-small"),
            vector_dim=vector_dim,
            api_dimensions=api_dimensions,
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
        body: dict[str, Any] = {"model": self._model, "input": texts}
        if self.api_dimensions:
            body["dimensions"] = self.api_dimensions
        resp = await self._client.post("/embeddings", json=body)
        resp.raise_for_status()
        data = resp.json()
        items = sorted(data["data"], key=lambda d: d["index"])
        return [list(d["embedding"]) for d in items]
