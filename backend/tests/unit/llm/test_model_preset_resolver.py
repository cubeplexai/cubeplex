"""Pure resolver: snapshot → ModelPreset selection."""

import pytest

from cubeplex.llm.config import ProviderConfig
from cubeplex.llm.errors import (
    BrokenPresetError,
    InvalidModelRefError,
    NoDefaultPresetError,
    UnknownPresetError,
)
from cubeplex.llm.resolver import parse_model_ref, resolve_model_preset, resolve_task_preset
from cubeplex.llm.snapshot import LLMSnapshot, ModelPreset


def _provider(slug: str, model_ids: tuple[str, ...]) -> ProviderConfig:
    return ProviderConfig.model_validate(
        {
            "base_url": "https://x",
            "api": "openai-completions",
            "models": [
                {
                    "id": mid,
                    "name": mid,
                    "contextWindow": 128000,
                    "maxTokens": 32000,
                }
                for mid in model_ids
            ],
        }
    )


def _snap(*presets: ModelPreset, task_routing: dict[str, str] | None = None) -> LLMSnapshot:
    # Build a provider map covering every ref in the supplied presets so
    # resolve_model_preset's broken-ref guard is satisfied. Tests that want
    # to exercise broken-ref behaviour construct LLMSnapshot directly.
    providers: dict[str, ProviderConfig] = {}
    by_slug: dict[str, set[str]] = {}
    for preset in presets:
        for ref in preset.chain:
            slug, _, model_id = ref.partition("/")
            if slug and model_id:
                by_slug.setdefault(slug, set()).add(model_id)
    for slug, mids in by_slug.items():
        providers[slug] = _provider(slug, tuple(sorted(mids)))
    return LLMSnapshot(
        providers=providers,
        model_presets=presets,
        task_routing=task_routing or {},
    )


def test_parse_model_ref_ok():
    assert parse_model_ref("anthropic/claude-opus-4-7") == ("anthropic", "claude-opus-4-7")


@pytest.mark.parametrize("bad", ["no-slash", "/leading", "trailing/", ""])
def test_parse_model_ref_invalid(bad):
    with pytest.raises(InvalidModelRefError):
        parse_model_ref(bad)


def test_model_preset_chain_is_primary_plus_fallbacks():
    p = ModelPreset(
        key="pro",
        primary="a/b",
        fallbacks=("c/d", "e/f"),
        kind="tier",
        is_default=True,
    )
    assert p.chain == ("a/b", "c/d", "e/f")


def test_resolve_none_returns_default():
    default = ModelPreset(key="pro", primary="a/b", fallbacks=(), kind="tier", is_default=True)
    mini = ModelPreset(key="lite", primary="c/d", fallbacks=(), kind="tier", is_default=False)
    assert resolve_model_preset(_snap(default, mini), None) is default


def test_resolve_by_key():
    default = ModelPreset(key="pro", primary="a/b", fallbacks=(), kind="tier", is_default=True)
    mini = ModelPreset(key="lite", primary="c/d", fallbacks=(), kind="tier", is_default=False)
    assert resolve_model_preset(_snap(default, mini), "lite") is mini


def test_resolve_unknown_key_raises():
    default = ModelPreset(key="pro", primary="a/b", fallbacks=(), kind="tier", is_default=True)
    with pytest.raises(UnknownPresetError, match="ghost"):
        resolve_model_preset(_snap(default), "ghost")


def test_resolve_no_default_raises():
    with pytest.raises(NoDefaultPresetError):
        resolve_model_preset(_snap(), None)


def test_resolve_task_preset_uses_task_routing():
    default = ModelPreset(key="pro", primary="a/b", fallbacks=(), kind="tier", is_default=True)
    mini = ModelPreset(key="lite", primary="c/d", fallbacks=(), kind="tier", is_default=False)
    snap = _snap(default, mini, task_routing={"title": "lite"})
    assert resolve_task_preset(snap, "title") is mini


def test_resolve_task_preset_falls_back_to_default_when_routing_empty():
    default = ModelPreset(key="pro", primary="a/b", fallbacks=(), kind="tier", is_default=True)
    assert resolve_task_preset(_snap(default), "compaction") is default


def test_resolve_broken_ref_raises():
    snap = LLMSnapshot(
        providers={},  # no providers → every ref is broken
        model_presets=(
            ModelPreset(key="pro", primary="ghost/x", fallbacks=(), kind="tier", is_default=True),
        ),
        task_routing={},
    )
    with pytest.raises(BrokenPresetError) as exc:
        resolve_model_preset(snap, None)
    assert "ghost/x" in exc.value.missing_refs
