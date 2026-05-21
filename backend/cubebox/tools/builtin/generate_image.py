"""generate_image tool — cubepi.AgentTool (image generation + artifact registration).

Factory pattern: call make_generate_image_tool(...) at agent-construction time.
The tool calls the cubepi image-generation subsystem, writes the result to the
sandbox, registers it as an artifact, and returns a downscaled copy to the model.
"""

from __future__ import annotations

import base64
import re
import shlex
from typing import Literal

from cubepi.agent.types import AgentTool, AgentToolResult
from cubepi.providers.base import ImageContent, TextContent
from cubepi.providers.images import generate_images
from cubepi.providers.images.types import ImagesContext, ImagesModel
from pydantic import BaseModel, Field

from cubebox.sandbox.base import Sandbox
from cubebox.services.artifact_registration import register_artifact_from_sandbox
from cubebox.services.attachments import resize_to_long_edge


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
    size: Literal["1024x1024", "1536x1024", "1024x1536", "auto"] = Field(
        default="auto",
        description="Output image dimensions. 'auto' lets the provider decide.",
    )
    quality: Literal["low", "medium", "high", "auto"] = Field(
        default="auto",
        description="Output image quality. 'auto' lets the provider decide.",
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
    images_model: ImagesModel,
    api_key: str | None,
) -> AgentTool[GenerateImageInput]:
    """Build the generate_image cubepi.AgentTool with bound dependencies.

    If images_model.api == 'openai-images', registers the OpenAI images provider
    at construction time using the supplied api_key.
    """
    if images_model.api == "openai-images":
        from cubepi.providers.images.openai_images import register_openai_images

        register_openai_images(api_key=api_key)

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

        # Read edit source images from the sandbox.
        input_images: list[ImageContent] = []
        for path in args.edit_source_paths:
            result = await sandbox.execute(f"base64 -w0 {shlex.quote(path)}")
            if result.exit_code in (None, 0) and result.output.strip():
                input_images.append(
                    ImageContent(source=result.output.strip(), media_type="image/png")
                )

        # Build per-call model with requested size/quality.
        model = images_model.model_copy(update={"size": args.size, "quality": args.quality})

        gen_result = await generate_images(
            model, ImagesContext(prompt=args.prompt, input_images=input_images)
        )

        if gen_result.stop_reason != "stop" or not gen_result.output:
            return AgentToolResult(
                content=[
                    TextContent(
                        text=(
                            f"Image generation failed: "
                            f"{gen_result.error_message or 'no image returned'}"
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
