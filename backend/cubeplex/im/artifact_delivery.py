"""Outbound artifact → IM delivery routing.

Decides how an agent-produced artifact reaches the chat: an inline image, a
native file message, or an HTTP share-link. The vocabulary matches the
``save_artifact`` guide (``cubeplex/prompts/artifacts.py``) and the share-link
HTML renderer's buckets (``api/routes/v1/artifact_share.py``); we re-list the
literals here because that renderer is an inline if-chain, not a classifier.
"""

from __future__ import annotations

from typing import Literal

OutboundKind = Literal["image", "file", "link"]

# Artifact types that are downloadable deliverables — sent as native files.
_FILE_TYPES = frozenset({"code", "document", "data", "skill", "file"})

# Per-platform native-file size caps (bytes). Over the cap → share-link
# fallback. Discord 25MB is the non-boosted-guild floor (boosted guilds allow
# more, but we use the safe floor).
_SIZE_CAPS: dict[str, int] = {
    "slack": 20 * 1024 * 1024,
    "discord": 25 * 1024 * 1024,
    "feishu": 30 * 1024 * 1024,
}
_DEFAULT_SIZE_CAP = 20 * 1024 * 1024


def artifact_outbound_kind(artifact_type: str) -> OutboundKind:
    """Route an artifact_type to its outbound delivery kind."""
    if artifact_type == "image":
        return "image"
    if artifact_type == "website":
        return "link"
    if artifact_type in _FILE_TYPES:
        return "file"
    return "link"


def outbound_size_cap(platform: str) -> int:
    """Native-file size cap for a platform, in bytes."""
    return _SIZE_CAPS.get(platform, _DEFAULT_SIZE_CAP)
