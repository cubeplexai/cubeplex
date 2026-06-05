"""Image provider builder — cubepi 0.7.

Picks a capability descriptor based on the image_generation.api string and
returns a (capability, base_url) pair ready to feed OpenAIImagesProvider.
"""

from __future__ import annotations

from cubepi.providers.images.capability import (
    ImagesCapabilityDescriptor,
    SizeSpec,
)


def build_image_capability(api: str) -> tuple[ImagesCapabilityDescriptor, str | None]:
    """Return (capability, default_base_url) for the given image-generation api.

    Accepts both the new short form ("openai", "doubao", ...) and the
    legacy long form that the existing config defaults to ("openai-images",
    "doubao-images", ...) — the latter is what ImageGenerationConfig.api
    defaults to today, so old configs keep working after the bump.
    """
    # Normalise legacy "<vendor>-images" → "<vendor>" so old configs work.
    api = api.removesuffix("-images")

    if api == "openai":
        return ImagesCapabilityDescriptor(), None

    if api == "doubao":
        return (
            ImagesCapabilityDescriptor(
                supports_seed=True,
                extra_payload={"watermark": False},
            ),
            "https://ark.cn-beijing.volces.com/api/v3",
        )

    if api == "siliconflow":
        return (
            ImagesCapabilityDescriptor(
                size_spec=SizeSpec(kind="image_size_string"),
                count_field="batch_size",
                supports_seed=True,
                supports_steps=True,
                steps_field="num_inference_steps",
                supports_guidance=True,
                guidance_field="guidance_scale",
                supports_negative_prompt=True,
                output_format_field=None,
            ),
            "https://api.siliconflow.cn/v1",
        )

    if api == "together":
        return (
            ImagesCapabilityDescriptor(
                size_spec=SizeSpec(kind="aspect_ratio"),
                supports_seed=True,
                supports_steps=True,
                steps_field="steps",
            ),
            "https://api.together.xyz/v1",
        )

    raise ValueError(f"Unsupported image_generation.api: {api!r}")
