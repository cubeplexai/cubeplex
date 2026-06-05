"""generate_image tool — ProviderError surfaces as is_error=True AgentToolResult."""

from __future__ import annotations

import pytest
from cubepi.errors import RateLimited
from cubepi.providers.images import FauxImagesProvider

from cubebox.tools.builtin.generate_image import GenerateImageInput, make_generate_image_tool


class _StubSandbox:
    async def execute(self, cmd: str, *, timeout: int | None = None) -> object:  # noqa: ANN001
        class _Result:
            exit_code = 0
            output = ""

        return _Result()

    async def upload(self, files: list[object]) -> None:
        return None


# 1x1 transparent PNG
_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4"
    "2mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg=="
)


@pytest.mark.asyncio
async def test_generate_image_provider_error_returns_is_error() -> None:
    provider = FauxImagesProvider(
        provider_id="faux",
        png_b64=_PNG_B64,
        raise_on_call=RateLimited,
    )
    model = provider.model("test")

    tool = make_generate_image_tool(
        org_id="org_t",
        workspace_id="ws_t",
        conversation_id="cv_t",
        sandbox=_StubSandbox(),  # type: ignore[arg-type]
        images_provider=provider,
        images_model=model,
    )

    result = await tool.execute("tc_0", GenerateImageInput(prompt="a cat"))

    assert result.is_error is True
    assert "failed" in result.content[0].text.lower()  # type: ignore[union-attr]
