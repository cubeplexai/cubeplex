"""Unit tests for resolve_task_model (Slice 2, §4.6).

Precedence: OrgSettings.task_models[task] > yaml config.llm.<task>_model > default.
"""

from typing import Any

import pytest

from cubebox.llm.config import LLMConfig, ProviderConfig
from cubebox.llm.factory import LLMFactory
from cubebox.models.org_settings import TASK_MODELS_KEY
from cubebox.models.org_settings import OrgSettings as DBS
from cubebox.services.task_model_resolver import resolve_task_model


def _providers() -> dict[str, ProviderConfig]:
    return {
        "anthropic": ProviderConfig(
            api="anthropic-messages", base_url="https://api.anthropic.com", api_key="sk-a"
        ),
        "haiku-provider": ProviderConfig(
            api="anthropic-messages", base_url="https://api.anthropic.com", api_key="sk-h"
        ),
        "yamlprov": ProviderConfig(
            api="openai-completions", base_url="https://y.example", api_key="sk-y"
        ),
    }


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalars(self) -> "_FakeResult":
        return self

    def all(self) -> list[Any]:
        return self._rows


class _FakeSession:
    """Fake AsyncSession.

    Returns the configured OrgSettings row only for a task_models query; every
    other query (provider/model loads, other settings keys) returns empty so the
    merged-config build degrades to yaml-only providers.
    """

    def __init__(self, task_models_value: dict[str, str] | None) -> None:
        self._task_models_value = task_models_value

    async def execute(self, stmt: Any) -> _FakeResult:
        sql = str(stmt).lower()
        is_settings = "org_settings" in sql
        if is_settings and self._task_models_value is not None:
            # Match the task_models key lookup; other settings keys → empty.
            row = DBS(org_id="org_test", key=TASK_MODELS_KEY, value=self._task_models_value)
            return _FakeResult([row])
        return _FakeResult([])

    async def get(self, *args: Any, **kwargs: Any) -> None:
        return None


def _factory(
    *,
    default_model: str = "anthropic/claude-sonnet-4",
    title_model: str | None = None,
    task_models: dict[str, str] | None = None,
    with_db: bool = True,
) -> LLMFactory:
    cfg = LLMConfig(
        default_model=default_model,
        title_model=title_model,
        providers=_providers(),
    )
    session = _FakeSession(task_models) if with_db else None
    return LLMFactory(
        llm_config=cfg,
        session=session,  # type: ignore[arg-type]
        org_id="org_test" if with_db else None,
    )


@pytest.mark.asyncio
async def test_orgsettings_task_model_wins_over_yaml_and_default() -> None:
    factory = _factory(
        title_model="yamlprov/yaml-title",
        task_models={"title": "haiku-provider/claude-haiku"},
    )
    provider, model, cfg = await resolve_task_model(factory, "title")
    assert (provider, model) == ("haiku-provider", "claude-haiku")
    assert cfg is factory.llm_config.providers["haiku-provider"]


@pytest.mark.asyncio
async def test_yaml_title_model_used_when_no_orgsettings() -> None:
    factory = _factory(title_model="yamlprov/yaml-title", task_models=None)
    provider, model, cfg = await resolve_task_model(factory, "title")
    assert (provider, model) == ("yamlprov", "yaml-title")
    assert cfg.base_url == "https://y.example"


@pytest.mark.asyncio
async def test_falls_back_to_default_when_nothing_configured() -> None:
    factory = _factory(task_models=None, title_model=None)
    provider, model, cfg = await resolve_task_model(factory, "title")
    assert (provider, model) == ("anthropic", "claude-sonnet-4")


@pytest.mark.asyncio
async def test_summarize_with_nothing_configured_returns_default() -> None:
    factory = _factory(task_models=None, title_model=None)
    provider, model, cfg = await resolve_task_model(factory, "summarize")
    assert (provider, model) == ("anthropic", "claude-sonnet-4")
