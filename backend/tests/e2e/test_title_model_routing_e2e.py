"""Title generation routes through ``resolve_task_model('title')``.

Title-gen is a latency-sensitive background task. Slice 2 (spec §4.6) lets
admins point it at a small/cheap model via ``OrgSettings.task_models['title']``
and forces reasoning off so a reasoning model can never stall it (the 30s
title-gen timeout incident).

This test does not need a live LLM. It stubs the cubepi provider and spies on
the task-model resolver to assert:

1. ``_generate_title`` resolves its provider/model via
   ``resolve_task_model(factory, "title")`` — i.e. the title task routing
   layer, not the raw default.
2. The cubepi ``provider.stream`` call carries ``StreamOptions(thinking="off")``
   so reasoning is disabled regardless of the configured model.
"""

from __future__ import annotations

from typing import Any

import pytest
from cubepi.providers.base import StreamOptions
from cubepi.providers.faux import FauxProvider, faux_assistant_message

import cubebox.services.task_model_resolver as task_model_resolver
from cubebox.services import conversation_title


def _make_fake_factory(provider: FauxProvider) -> object:
    """Minimal LLMFactory stand-in carrying org context for the resolver."""

    class _FakeProviderConfig:
        name = "faux"

    class _FakeFactory:
        _session = None
        _org_id = "org-x"

        def build_cubepi_provider(
            self,
            provider_config: object,
            *,
            provider_name: str = "",
            cache_policy: object = None,
        ) -> FauxProvider:
            return provider

    return _FakeFactory()


@pytest.mark.asyncio
async def test_title_generation_routes_via_resolve_task_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = FauxProvider()
    provider.set_responses([faux_assistant_message("Trip planning to Kyoto")])

    # --- spy on the task-model resolver -----------------------------------
    calls: list[tuple[object, str]] = []

    class _StubProviderConfig:
        name = "faux"

    async def _spy_resolve_task_model(factory: object, task: str) -> tuple[str, str, Any]:
        calls.append((factory, task))
        return ("faux", "small-title-model", _StubProviderConfig())

    monkeypatch.setattr(task_model_resolver, "resolve_task_model", _spy_resolve_task_model)

    # --- capture the StreamOptions passed to provider.stream --------------
    captured: dict[str, Any] = {}
    original_stream = provider.stream

    async def _spy_stream(*args: Any, **kwargs: Any) -> Any:
        captured["options"] = kwargs.get("options")
        return await original_stream(*args, **kwargs)

    monkeypatch.setattr(provider, "stream", _spy_stream)

    factory = _make_fake_factory(provider)
    text = await conversation_title._generate_title(
        factory,  # type: ignore[arg-type]
        "title prompt",
        org_id="org-x",
    )

    # (a) routed through the "title" task layer, not the raw default
    assert len(calls) == 1
    assert calls[0][1] == "title"

    # (b) reasoning forced off
    options = captured["options"]
    assert isinstance(options, StreamOptions)
    assert options.thinking == "off"

    # sanity: streamed text still flows through
    assert "Kyoto" in text
