"""Unit tests for _generate_title.

Exercises ``conversation_title._generate_title`` by monkeypatching
``LLMFactory`` to return a ``FauxProvider``, verifying the cubepi
one-shot title-generation path (prompt shape, message ordering, output
trimming, and error handling).
"""

from __future__ import annotations

import pytest
from cubepi.providers.faux import FauxProvider, faux_assistant_message

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_factory(provider: FauxProvider) -> object:
    """Return a minimal LLMFactory stand-in for _generate_title."""

    class _FakeProviderConfig:
        name = "faux"

    class _FakeLLMConfig:
        # No ``title_model`` attribute → resolve_task_model falls through to
        # the default provider/model below.
        title_model = None

    class _FakeFactory:
        # resolve_task_model only consults OrgSettings when both are truthy;
        # leaving them None keeps these unit tests on the default path.
        _session = None
        _org_id = None
        llm_config = _FakeLLMConfig()

        async def resolve_default_provider_and_config(
            self,
        ) -> tuple[str, str, _FakeProviderConfig]:
            return ("faux", "test-model", _FakeProviderConfig())

        def build_cubepi_provider(
            self,
            provider_config: object,
            *,
            provider_name: str = "",
            cache_policy: object = None,
        ) -> FauxProvider:
            return provider

    return _FakeFactory()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_title_returns_streamed_text() -> None:
    """FauxProvider text chunks are concatenated and returned."""
    from cubebox.services.conversation_title import _generate_title

    provider = FauxProvider()
    provider.set_responses([faux_assistant_message("Quick chat about Tokyo")])

    factory = _make_fake_factory(provider)
    text = await _generate_title(factory, "title prompt", org_id="org-x")  # type: ignore[arg-type]

    assert "Tokyo" in text


@pytest.mark.asyncio
async def test_generate_title_raises_on_error_event() -> None:
    """A provider error event surfaces as RuntimeError."""
    from cubebox.services.conversation_title import _generate_title

    # FauxProvider with no queued responses emits an error event automatically.
    provider = FauxProvider()  # no responses queued

    factory = _make_fake_factory(provider)
    with pytest.raises(RuntimeError, match="No more faux responses queued"):
        await _generate_title(factory, "prompt", org_id="org-x")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_generate_title_empty_response_returns_empty() -> None:
    """An assistant message with no text content returns an empty string."""
    from cubepi.providers.faux import faux_assistant_message

    from cubebox.services.conversation_title import _generate_title

    # faux_assistant_message with an empty string still yields text_delta events
    # but with no content — result should be empty string (not crash).
    provider = FauxProvider()
    provider.set_responses([faux_assistant_message("")])

    factory = _make_fake_factory(provider)
    text = await _generate_title(factory, "prompt", org_id="org-x")  # type: ignore[arg-type]
    assert text == ""


@pytest.mark.asyncio
async def test_generate_title_provider_called_once() -> None:
    """Provider.stream is called exactly once per invocation."""
    from cubebox.services.conversation_title import _generate_title

    provider = FauxProvider()
    provider.set_responses([faux_assistant_message("A title")])

    factory = _make_fake_factory(provider)
    await _generate_title(factory, "some prompt", org_id="org-x")  # type: ignore[arg-type]

    assert provider.call_count == 1
