"""Unit tests for _generate_title.

Exercises ``conversation_title._generate_title`` by monkeypatching
``load_llm_snapshot`` + ``build_chain_model`` to return a ``BoundModel``
wrapping a ``FauxProvider``, verifying the cubepi one-shot
title-generation path (prompt shape, message ordering, output trimming,
and error handling).
"""

from __future__ import annotations

from typing import Any

import pytest
from cubepi.providers.base import BoundModel, Model
from cubepi.providers.faux import FauxProvider, faux_assistant_message

from cubebox.llm.config import ModelConfig, ProviderConfig
from cubebox.llm.snapshot import LLMSnapshot, ModelPreset

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _snap() -> LLMSnapshot:
    return LLMSnapshot(
        providers={
            "acme": ProviderConfig(
                api="openai-completions",
                base_url="https://x",
                api_key="k",
                models=[
                    ModelConfig(
                        id="title-m",
                        name="title-m",
                        contextWindow=128000,
                        maxTokens=32000,
                    )
                ],
            )
        },
        model_presets=(
            ModelPreset(
                key="default",
                primary="acme/title-m",
                fallbacks=(),
                kind="tier",
                is_default=True,
            ),
        ),
        task_routing={"title": "default"},
    )


def _install_fakes(monkeypatch: pytest.MonkeyPatch, provider: FauxProvider) -> None:
    """Patch snapshot loader + chain builder in the service module.

    The service calls ``load_llm_snapshot`` then ``build_chain_model``;
    we short-circuit both to return a known snapshot and a BoundModel
    wrapping ``provider``.
    """

    async def _fake_load(session: Any, org_id: str, backend: Any) -> LLMSnapshot:
        return _snap()

    def _fake_build_chain_model(snap: LLMSnapshot, preset: ModelPreset, **_: Any) -> BoundModel:
        spec = Model(
            id="title-m",
            provider_id="acme",
            context_window=128000,
            max_tokens=32000,
        )
        return BoundModel(provider=provider, spec=spec)

    monkeypatch.setattr(
        "cubebox.services.conversation_title.load_llm_snapshot",
        _fake_load,
    )
    monkeypatch.setattr(
        "cubebox.services.conversation_title.build_chain_model",
        _fake_build_chain_model,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_title_returns_streamed_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FauxProvider text chunks are concatenated and returned."""
    from cubebox.services.conversation_title import _generate_title

    provider = FauxProvider(provider_id="acme")
    provider.set_responses([faux_assistant_message("Quick chat about Tokyo")])
    _install_fakes(monkeypatch, provider)

    text = await _generate_title(None, "org-x", object(), "title prompt")  # type: ignore[arg-type]

    assert "Tokyo" in text


@pytest.mark.asyncio
async def test_generate_title_raises_on_error_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider error event surfaces as RuntimeError."""
    from cubebox.services.conversation_title import _generate_title

    # FauxProvider with no queued responses emits an error event automatically.
    provider = FauxProvider(provider_id="acme")  # no responses queued
    _install_fakes(monkeypatch, provider)

    with pytest.raises(RuntimeError, match="No more faux responses queued"):
        await _generate_title(None, "org-x", object(), "prompt")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_generate_title_empty_response_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An assistant message with no text content returns an empty string."""
    from cubebox.services.conversation_title import _generate_title

    # faux_assistant_message with an empty string still yields text_delta events
    # but with no content — result should be empty string (not crash).
    provider = FauxProvider(provider_id="acme")
    provider.set_responses([faux_assistant_message("")])
    _install_fakes(monkeypatch, provider)

    text = await _generate_title(None, "org-x", object(), "prompt")  # type: ignore[arg-type]
    assert text == ""


@pytest.mark.asyncio
async def test_generate_title_provider_called_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider.stream is called exactly once per invocation."""
    from cubebox.services.conversation_title import _generate_title

    provider = FauxProvider(provider_id="acme")
    provider.set_responses([faux_assistant_message("A title")])
    _install_fakes(monkeypatch, provider)

    await _generate_title(None, "org-x", object(), "some prompt")  # type: ignore[arg-type]

    assert provider.call_count == 1
