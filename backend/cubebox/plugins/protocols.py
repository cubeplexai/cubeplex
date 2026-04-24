"""Plugin Protocols + supporting dataclasses + version constant.

CUBEBOX_PLUGIN_API_VERSION is the single integer plugins must declare via
their PluginManifest. Mismatch → registry refuses to load the plugin.
"""

from dataclasses import dataclass, field  # noqa: F401
from datetime import datetime  # noqa: F401
from pathlib import Path  # noqa: F401
from typing import Any, Final, Protocol, runtime_checkable  # noqa: F401
from uuid import UUID  # noqa: F401

from fastapi import APIRouter, Request  # noqa: F401

CUBEBOX_PLUGIN_API_VERSION: Final[int] = 1


@dataclass(frozen=True)
class PluginManifest:
    """Plugin self-describing metadata. Required entry_point per wheel."""

    api_version: int
    name: str
    version: str
    description: str = ""
