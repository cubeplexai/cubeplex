"""generate_image tool — cubepi.AgentTool (image generation + artifact registration).

Factory pattern: call make_generate_image_tool(...) at agent-construction time.
The tool calls the supplied images_provider instance, writes the result to the
sandbox, registers it as an artifact, and returns a downscaled copy to the model.
"""

from __future__ import annotations

import base64
import re
import shlex
from typing import Protocol

from cubepi.agent.types import AgentTool, AgentToolResult
from cubepi.errors import ProviderError
from cubepi.providers.base import ImageContent, TextContent
from cubepi.providers.images import (
    AssistantImages,
    ImagesContext,
    ImagesModel,
    ImagesOptions,
)
from pydantic import BaseModel, Field

from cubebox.sandbox.base import Sandbox
from cubebox.services.artifact_registration import register_artifact_from_sandbox
from cubebox.services.attachments import resize_to_long_edge


class _ImagesProvider(Protocol):
    """Structural type for a cubepi images provider instance."""

    async def generate_images(
        self,
        model: ImagesModel,
        context: ImagesContext,
        *,
        options: ImagesOptions | None = None,
    ) -> AssistantImages: ...


class GenerateImageInput(BaseModel):
    """Input schema for generate_image."""

    prompt: str = Field(..., description="Text description of the image to generate or edit.")
    edit_source_paths: list[str] = Field(
        default_factory=list,
        description=(
            "Sandbox paths of existing images to edit. "
            "When provided, the first path is overwritten with the generated result."
        ),
    )
    size: str | None = Field(
        default=None,
        description=(
            "Output image dimensions — passed through to the provider. "
            "Examples: '1024x1024', '1536x864', '1024x1536', or an aspect ratio like '16:9'. "
            "Omit to let the provider decide."
        ),
    )
    quality: str | None = Field(
        default=None,
        description=(
            "Output image quality — provider-interpreted. "
            "Examples: 'low', 'medium', 'high'. Omit to let the provider decide."
        ),
    )


def _slug_from_prompt(prompt: str, max_len: int = 40) -> str:
    """Convert a prompt to a filesystem-safe slug, max_len chars, fallback 'image'."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", prompt).strip("-").lower()
    slug = slug[:max_len].rstrip("-")
    return slug or "image"


def make_generate_image_tool(
    *,
    org_id: str,
    workspace_id: str,
    conversation_id: str,
    sandbox: Sandbox,
    images_provider: _ImagesProvider,
    images_model: ImagesModel,
) -> AgentTool[GenerateImageInput]:
    """Build the generate_image cubepi.AgentTool with bound dependencies.

    images_provider must be a per-run instance (e.g. OpenAIImagesProvider or
    FauxImagesProvider) created by the caller — never the global registry.
    """

    async def _execute(
        tool_call_id: str,
        args: GenerateImageInput,
        *,
        signal: object = None,
        on_update: object = None,
    ) -> AgentToolResult:
        del tool_call_id, signal

        if on_update is not None:
            try:
                import asyncio
                import inspect

                callable_update = on_update
                coro = callable_update("Generating image…")  # type: ignore[operator]
                if inspect.isawaitable(coro):
                    await asyncio.shield(coro)
            except Exception:  # noqa: BLE001
                pass

        # Read edit source images from the sandbox. A missing/unreadable source must
        # fail fast — silently dropping it would turn an edit into a fresh generation
        # and then overwrite edit_source_paths[0] with unrelated content.
        input_images: list[ImageContent] = []
        for path in args.edit_source_paths:
            result = await sandbox.execute(f"base64 -w0 {shlex.quote(path)}")
            if result.exit_code not in (None, 0) or not result.output.strip():
                return AgentToolResult(
                    content=[TextContent(text=f"Could not read edit source image: {path}")],
                    is_error=True,
                )
            input_images.append(ImageContent(source=result.output.strip(), media_type="image/png"))

        try:
            gen_result = await images_provider.generate_images(
                images_model,
                ImagesContext(
                    prompt=args.prompt,
                    input_images=input_images,
                    size=args.size,
                    quality=args.quality,  # type: ignore[arg-type]
                ),
            )
        except ProviderError as exc:
            return AgentToolResult(
                content=[TextContent(text=f"Image generation failed: {exc}")],
                is_error=True,
            )

        if gen_result.stop_reason == "aborted" or not gen_result.output:
            return AgentToolResult(
                content=[
                    TextContent(
                        text=(
                            "Image generation was aborted before completion."
                            if gen_result.stop_reason == "aborted"
                            else "Image generation returned no output."
                        )
                    )
                ],
                is_error=True,
            )

        gen = gen_result.output[0]
        if not isinstance(gen, ImageContent):
            return AgentToolResult(
                content=[TextContent(text="Image generation failed: unexpected output type")],
                is_error=True,
            )
        full_bytes = base64.b64decode(gen.source)

        # Determine sandbox write path.
        if args.edit_source_paths:
            target_path = args.edit_source_paths[0]
        else:
            slug = _slug_from_prompt(args.prompt)
            target_path = f"/work/{slug}.png"

        await sandbox.upload([(target_path, full_bytes)])

        artifact = await register_artifact_from_sandbox(
            sandbox=sandbox,
            conversation_id=conversation_id,
            org_id=org_id,
            workspace_id=workspace_id,
            name=target_path.rsplit("/", 1)[-1],
            artifact_type="image",
            path=target_path,
            description=args.prompt,
        )

        small = resize_to_long_edge(full_bytes, target=1568, jpeg_quality=85)
        small_b64 = base64.b64encode(small).decode("ascii")

        return AgentToolResult(
            content=[
                TextContent(
                    text=(
                        f"Generated image artifact id={artifact.id} v{artifact.version} "
                        f"at {target_path}"
                    )
                ),
                ImageContent(source=small_b64, media_type="image/jpeg"),
            ]
        )

    return AgentTool(
        name="generate_image",
        description=(
            "Generate an image from a text prompt, or edit existing image(s) by passing "
            "their sandbox paths in edit_source_paths. The image is saved as an artifact "
            "the user can preview and is returned for further editing."
        ),
        parameters=GenerateImageInput,
        execute=_execute,
    )
