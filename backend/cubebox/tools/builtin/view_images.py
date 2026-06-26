"""view_images tool ported to cubepi.AgentTool (M2.1).

Uses a factory pattern because the tool requires per-request DI:
org_id, workspace_id, ObjectStoreClient, and LLMCapabilities.
Call make_view_images_tool(...) at agent-construction time to obtain the
cubepi.AgentTool instance.
"""

from __future__ import annotations

import base64
from typing import Any, Literal

from cubepi.agent.types import AgentTool, AgentToolResult
from cubepi.providers.base import ImageContent, TextContent
from pydantic import BaseModel, Field

from cubebox.llm.capabilities import LLMCapabilities
from cubebox.objectstore.client import ObjectStoreClient
from cubebox.services.attachments import resize_to_long_edge


class ViewImagesInput(BaseModel):
    """Input schema for view_images (cubepi variant)."""

    paths: list[str] = Field(
        ...,
        min_length=1,
        max_length=8,
        description=(
            "Sandbox file paths of images to view — both images you created/"
            "processed in your sandbox and user-uploaded attachments (from the "
            "[Attachments] hint)."
        ),
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
    sandbox: Any | None = None,
    max_long_edge: int = 1568,
    jpeg_quality: int = 85,
) -> AgentTool[ViewImagesInput]:
    """Build the view_images cubepi.AgentTool with bound dependencies.

    Resolution is sandbox-first: a path is read straight from the run's sandbox
    filesystem when a sandbox is bound (``sandbox`` is the run's SandboxBackend),
    so the model can see images it created/processed in its own sandbox — not
    just user-uploaded attachments. Falls back to the conversation-attachment
    object store (the only source when ``sandbox is None``).

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

        from cubebox.db.engine import async_session_maker
        from cubebox.repositories import AttachmentRepository

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
                data: bytes | None = None
                label = path
                dims: tuple[int, int] | None = None

                # Sandbox-first: read the file straight from the run's sandbox FS.
                # Covers images the agent created/processed in its sandbox AND
                # hydrated attachments (which also land on the sandbox FS), with
                # no object-store round-trip.
                if sandbox is not None:
                    try:
                        files = await sandbox.download([path])
                        if files and files[0][1]:
                            data = files[0][1]
                    except Exception:  # noqa: BLE001 — fall through to the attachment store
                        data = None

                # Fallback: the conversation-attachment store (object store + DB dims).
                if data is None:
                    row = await repo.find_by_sandbox_path(path)
                    if row is not None and row.kind == "image":
                        try:
                            data, _ = await objectstore.download_file(row.object_key)
                            label = row.filename
                            dims = (row.width or 0, row.height or 0)
                        except Exception as exc:  # noqa: BLE001
                            from loguru import logger

                            logger.exception("view_images attachment fetch failed for {}", path)
                            content_blocks.append(
                                TextContent(text=f"[{idx}] {path}: error — {exc}")
                            )
                            continue

                if data is None:
                    content_blocks.append(
                        TextContent(text=f"[{idx}] {path}: error — image not found")
                    )
                    continue

                try:
                    if args.detail == "auto" and dims and dims[0] <= 768 and dims[1] <= 768:
                        target = max(dims)
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
                            text=f"[{idx}] {label} (target {target}px, jpeg q={jpeg_quality})"
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
            "Load and inspect one or more images by sandbox path — images you "
            "created or processed in your sandbox (screenshots, renders, downloads) "
            "as well as user-uploaded attachments (from the [Attachments] hint). "
            "Returns the images in a multimodal tool result for the next reasoning step."
        ),
        parameters=ViewImagesInput,
        execute=_execute,
    )
