"""view_images tool ported to cubepi.AgentTool (M2.1).

Uses a factory pattern because the tool requires per-request DI:
org_id, workspace_id, ObjectStoreClient, and LLMCapabilities.
Call make_view_images_tool(...) at agent-construction time to obtain the
cubepi.AgentTool instance.
"""

from __future__ import annotations

import base64
from typing import Literal

from cubepi.agent.types import AgentTool, AgentToolResult
from cubepi.providers.base import ImageContent, TextContent
from pydantic import BaseModel, Field

from cubeplex.llm.capabilities import LLMCapabilities
from cubeplex.objectstore.client import ObjectStoreClient
from cubeplex.services.attachments import resize_to_long_edge


class ViewImagesInput(BaseModel):
    """Input schema for view_images (cubepi variant)."""

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


def _resolve_target(detail: str, default: int = 1568) -> int:
    if detail == "low":
        return 512
    return default


def make_view_images_tool(
    *,
    org_id: str,
    workspace_id: str,
    objectstore: ObjectStoreClient,
    capabilities: LLMCapabilities,
    max_long_edge: int = 1568,
    jpeg_quality: int = 85,
) -> AgentTool[ViewImagesInput]:
    """Build the view_images cubepi.AgentTool with bound dependencies.

    A fresh DB session is opened per call. org_id / workspace_id are
    bound at construction (run-scoped).
    """

    async def _execute(
        tool_call_id: str,
        args: ViewImagesInput,
        *,
        signal: object = None,
        on_update: object = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update

        if not capabilities.supports_image():
            return AgentToolResult(
                content=[
                    TextContent(
                        text=(
                            "Error: the current model and fallbacks do not support image input. "
                            "Cannot view images."
                        )
                    )
                ],
                is_error=True,
            )

        from cubeplex.db.engine import async_session_maker
        from cubeplex.repositories import AttachmentRepository

        content_blocks: list[TextContent | ImageContent] = [
            TextContent(text=f"Loaded {len(args.paths)} image(s):")
        ]

        async with async_session_maker() as session:
            repo = AttachmentRepository(
                session,
                org_id=org_id,
                workspace_id=workspace_id,
            )
            for idx, path in enumerate(args.paths, 1):
                row = await repo.find_by_sandbox_path(path)
                if row is None or row.kind != "image":
                    content_blocks.append(
                        TextContent(text=f"[{idx}] {path}: error — image not found")
                    )
                    continue
                try:
                    data, _ = await objectstore.download_file(row.object_key)
                    if (
                        args.detail == "auto"
                        and (row.width or 0) <= 768
                        and (row.height or 0) <= 768
                    ):
                        target = max(row.width or 0, row.height or 0)
                    else:
                        resolved_detail = "high" if args.detail == "auto" else args.detail
                        target = _resolve_target(resolved_detail, default=max_long_edge)
                    resized = resize_to_long_edge(
                        data,
                        target=target,
                        jpeg_quality=jpeg_quality,
                    )
                    b64 = base64.b64encode(resized).decode("ascii")
                    content_blocks.append(
                        TextContent(
                            text=f"[{idx}] {row.filename} (target {target}px, jpeg q={jpeg_quality})"
                        )
                    )
                    content_blocks.append(
                        ImageContent(
                            source=b64,
                            media_type="image/jpeg",
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    from loguru import logger

                    logger.exception("view_images failed for {}", path)
                    content_blocks.append(TextContent(text=f"[{idx}] {path}: error — {exc}"))

        return AgentToolResult(content=content_blocks)

    return AgentTool(
        name="view_images",
        description=(
            "Load and inspect one or more image attachments the user uploaded. "
            "Pass sandbox paths from the [Attachments] hint. Returns the images "
            "in a multimodal tool result for the next reasoning step."
        ),
        parameters=ViewImagesInput,
        execute=_execute,
    )
