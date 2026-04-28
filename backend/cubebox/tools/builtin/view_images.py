"""view_images tool — load attachment images into a multimodal ToolMessage."""

from __future__ import annotations

import base64
from typing import Any, Literal

from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool
from loguru import logger
from pydantic import BaseModel, Field

from cubebox.config import config
from cubebox.llm.capabilities import LLMCapabilities
from cubebox.objectstore.client import ObjectStoreClient
from cubebox.repositories import AttachmentRepository
from cubebox.services.attachments import resize_to_long_edge


class ViewImagesInput(BaseModel):
    """Input schema for view_images."""

    paths: list[str] = Field(
        ...,
        min_length=1,
        max_length=8,
        description="Sandbox paths of attachment images (from [Attachments] hint).",
    )
    detail: Literal["auto", "low", "high"] = Field(
        default="auto",
        description=(
            "low: ≤512px (cheap scan). "
            "high: ≤1568px (analysis). "
            "auto: server picks based on original size."
        ),
    )


def _resolve_target(detail: str) -> int:
    if detail == "low":
        return 512
    return int(config.get("attachments.view_images.max_long_edge", 1568))


def _quality() -> int:
    return int(config.get("attachments.view_images.jpeg_quality", 85))


def make_view_images_tool(
    *,
    org_id: str,
    workspace_id: str,
    objectstore: ObjectStoreClient,
    capabilities: LLMCapabilities,
) -> StructuredTool:
    """Build the view_images StructuredTool. A fresh DB session is opened per call.

    org_id / workspace_id are bound at construction (run-scoped); the session is
    short-lived to avoid holding connections across the agent loop.
    """

    async def view_images(paths: list[str], detail: str = "auto") -> ToolMessage:
        if not capabilities.supports_image():
            return ToolMessage(
                content=(
                    "Error: the current model and fallbacks do not support image input. "
                    "Cannot view images."
                ),
                tool_call_id="",
                status="error",
            )

        from cubebox.db.engine import async_session_maker

        out_blocks: list[str | dict[str, Any]] = [
            {"type": "text", "text": f"Loaded {len(paths)} image(s):"},
        ]

        async with async_session_maker() as session:
            repo = AttachmentRepository(
                session,
                org_id=org_id,
                workspace_id=workspace_id,
            )
            for idx, path in enumerate(paths, 1):
                row = await repo.find_by_sandbox_path(path)
                if row is None or row.kind != "image":
                    out_blocks.append(
                        {
                            "type": "text",
                            "text": f"[{idx}] {path}: error — image not found",
                        }
                    )
                    continue
                try:
                    data, _ = await objectstore.download_file(row.object_key)
                    if detail == "auto" and (row.width or 0) <= 768 and (row.height or 0) <= 768:
                        target = max(row.width or 0, row.height or 0)
                    else:
                        target = _resolve_target("high" if detail == "auto" else detail)
                    resized = resize_to_long_edge(
                        data,
                        target=target,
                        jpeg_quality=_quality(),
                    )
                    b64 = base64.b64encode(resized).decode("ascii")
                    out_blocks.append(
                        {
                            "type": "text",
                            "text": (
                                f"[{idx}] {row.filename} (target {target}px, jpeg q={_quality()})"
                            ),
                        }
                    )
                    out_blocks.append(
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": b64,
                            },
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.exception("view_images failed for {}", path)
                    out_blocks.append(
                        {
                            "type": "text",
                            "text": f"[{idx}] {path}: error — {exc}",
                        }
                    )

        return ToolMessage(content=out_blocks, tool_call_id="")

    return StructuredTool.from_function(
        coroutine=view_images,
        name="view_images",
        description=(
            "Load and inspect one or more image attachments the user uploaded. "
            "Pass sandbox paths from the [Attachments] hint. Returns the images "
            "in a multimodal tool result for the next reasoning step."
        ),
        args_schema=ViewImagesInput,
    )
