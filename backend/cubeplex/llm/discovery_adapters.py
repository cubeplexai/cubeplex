"""Collect exact instance identities without inferring model capabilities."""

from __future__ import annotations

from typing import Any

from cubeplex.llm.discovery_transport import (
    MAX_MODELS,
    MAX_PAGES,
    DiscoveryFailure,
    DiscoveryTransport,
    discovery_url,
)
from cubeplex.llm.discovery_types import CallingConfiguration, DiscoveryPolicy


async def collect_models(
    config: CallingConfiguration, policy: DiscoveryPolicy, transport: DiscoveryTransport
) -> list[dict[str, Any]]:
    if policy.adapter == "static":
        return []
    url = discovery_url(config.base_url, policy)
    rows: list[dict[str, Any]] = []
    ids: set[str] = set()
    cursors: set[str] = set()
    cursor: str | None = None
    for _page in range(MAX_PAGES):
        params: dict[str, str | int] | None = None
        if policy.adapter == "anthropic":
            params = {"limit": 1000}
            if cursor:
                params["after_id"] = cursor
        payload = await transport.request("GET", url, params=params)
        items = payload.get("models" if policy.adapter == "ollama" else "data")
        if not isinstance(items, list):
            raise DiscoveryFailure("malformed_response", "The model list has an unsupported shape.")
        for item in items:
            if not isinstance(item, dict):
                raise DiscoveryFailure("malformed_response", "A model-list entry is invalid.")
            model_id = item.get("name" if policy.adapter == "ollama" else "id")
            if not isinstance(model_id, str) or not model_id.strip():
                raise DiscoveryFailure("malformed_response", "A model-list entry has no identity.")
            ids.add(model_id)
            if len(ids) > MAX_MODELS:
                raise DiscoveryFailure(
                    "discovery_limit", "The instance has too many models to collect."
                )
            rows.append({**item, "id": model_id})
        if policy.adapter != "anthropic":
            return rows
        more = payload.get("has_more")
        if not isinstance(more, bool):
            raise DiscoveryFailure("malformed_response", "The paginated model list is incomplete.")
        if not more:
            return rows
        next_cursor = payload.get("last_id")
        if not isinstance(next_cursor, str) or not next_cursor or next_cursor in cursors:
            raise DiscoveryFailure(
                "malformed_response", "The model list repeats or omits its cursor."
            )
        cursors.add(next_cursor)
        cursor = next_cursor
    raise DiscoveryFailure("discovery_limit", "The model list exceeds the page limit.")
