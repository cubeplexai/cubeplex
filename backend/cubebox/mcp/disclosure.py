"""MCP progressive disclosure — config, threshold gate."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

from cubebox.config import config


@dataclass(frozen=True)
class DisclosureSettings:
    enabled: Literal["auto", "on", "off"] = "auto"
    threshold_pct: float = 10.0
    min_servers: int = 2


_EnabledLiteral = Literal["auto", "on", "off"]


def load_disclosure_settings() -> DisclosureSettings:
    raw_enabled = str(config.get("mcp.progressive_disclosure.enabled", "auto"))
    if raw_enabled not in ("auto", "on", "off"):
        raw_enabled = "auto"
    return DisclosureSettings(
        enabled=cast(_EnabledLiteral, raw_enabled),
        threshold_pct=float(config.get("mcp.progressive_disclosure.threshold_pct", 10.0)),
        min_servers=int(config.get("mcp.progressive_disclosure.min_servers", 2)),
    )


def disclosure_active(
    settings: DisclosureSettings,
    *,
    server_count: int,
    total_tool_tokens: int = 0,
    context_window: int = 0,
) -> bool:
    """True when the catalog/expand machinery replaces eager tool loading."""
    if settings.enabled == "off":
        return False
    if settings.enabled == "on":
        return True
    # "auto": both guards must pass.
    if server_count < settings.min_servers:
        return False
    if context_window <= 0:
        return server_count >= settings.min_servers
    return (total_tool_tokens / context_window * 100) >= settings.threshold_pct
