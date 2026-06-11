"""Conversation sharing — snapshot builder and artifact copier."""

from __future__ import annotations

import copy
from typing import Any

from loguru import logger

from cubebox.agents.checkpointer import init_checkpointer
from cubebox.objectstore.client import get_objectstore_client

_ATTACHMENT_KEEP_KEYS = {"filename", "mime_type", "size"}


def filter_messages_for_snapshot(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Filter and sanitize messages for public snapshot.

    Excludes system messages and synthetic messages. Strips attachment
    file content/URLs (keeps only metadata shell). Keeps everything else
    (user, assistant, tool_call, tool_result, thinking, citations).
    """
    result: list[dict[str, Any]] = []
    for msg in messages:
        if msg.get("role") == "system":
            continue
        metadata = msg.get("metadata") or {}
        if metadata.get("synthetic"):
            continue

        msg = copy.deepcopy(msg)
        metadata = msg.get("metadata") or {}

        attachments = metadata.get("attachments")
        if attachments and isinstance(attachments, list):
            metadata["attachments"] = [
                {k: v for k, v in att.items() if k in _ATTACHMENT_KEEP_KEYS}
                for att in attachments
                if isinstance(att, dict)
            ]

        result.append(msg)
    return result


async def build_snapshot(conversation_id: str) -> list[dict[str, Any]]:
    """Load messages from cubepi checkpointer, filter for public snapshot."""
    async with init_checkpointer() as cp:
        data = await cp.load(conversation_id)
    if data is None:
        return []
    raw = [m.model_dump(mode="json") for m in data.messages]
    return filter_messages_for_snapshot(raw)


async def copy_artifacts_to_share(
    share_id: str,
    conversation_id: str,
    artifacts: list[dict[str, Any]],
) -> None:
    """Copy artifact files from conversation storage to share-scoped storage."""
    store = get_objectstore_client()
    for art in artifacts:
        art_id = art["id"]
        version = art.get("version", 1)
        src_prefix = f"artifacts/{conversation_id}/{art_id}/v{version}/"
        dst_prefix = f"shares/{share_id}/artifacts/{art_id}/v{version}/"

        keys = await store.list_objects(src_prefix)
        for key in keys:
            data, content_type = await store.download_file(key)
            rel = key[len(src_prefix) :]
            dst_key = f"{dst_prefix}{rel}"
            await store.upload_file(dst_key, data, content_type)

        logger.debug(
            "Copied {} artifact file(s) for {} to share {}",
            len(keys),
            art_id,
            share_id,
        )
