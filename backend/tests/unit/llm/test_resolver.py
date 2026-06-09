"""Pure resolver: snapshot → LLMPreset selection."""

import pytest

from cubebox.llm.config import ProviderConfig
from cubebox.llm.errors import (
    BrokenPresetError,
    InvalidModelRefError,
    NoDefaultPresetError,
    UnknownPresetError,
)
from cubebox.llm.resolver import parse_model_ref, resolve_preset, resolve_task_preset
from cubebox.llm.snapshot import LLMPreset, LLMSnapshot


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


def _snap(*presets: LLMPreset, task_presets: dict[str, str] | None = None) -> LLMSnapshot:
    # Build a provider map covering every ref in the supplied presets so
    # resolve_preset's broken-ref guard is satisfied. Tests that want to
    # exercise broken-ref behaviour construct LLMSnapshot directly.
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
        presets=presets,
        task_presets=task_presets or {},
    )


def test_parse_model_ref_ok():
    assert parse_model_ref("anthropic/claude-opus-4-7") == ("anthropic", "claude-opus-4-7")


@pytest.mark.parametrize("bad", ["no-slash", "/leading", "trailing/", ""])
def test_parse_model_ref_invalid(bad):
    with pytest.raises(InvalidModelRefError):
        parse_model_ref(bad)


def test_resolve_preset_none_returns_default():
    default = LLMPreset(label="default", chain=("a/b",), is_default=True)
    mini = LLMPreset(label="mini", chain=("c/d",), is_default=False)
    assert resolve_preset(_snap(default, mini), None) is default


def test_resolve_preset_label_match():
    default = LLMPreset(label="default", chain=("a/b",), is_default=True)
    mini = LLMPreset(label="mini", chain=("c/d",), is_default=False)
    assert resolve_preset(_snap(default, mini), "mini") is mini


def test_resolve_preset_unknown_label():
    default = LLMPreset(label="default", chain=("a/b",), is_default=True)
    with pytest.raises(UnknownPresetError, match="ghost"):
        resolve_preset(_snap(default), "ghost")


def test_resolve_preset_no_default_raises():
    with pytest.raises(NoDefaultPresetError):
        resolve_preset(_snap(), None)


def test_resolve_task_preset_uses_task_mapping():
    default = LLMPreset(label="default", chain=("a/b",), is_default=True)
    mini = LLMPreset(label="mini", chain=("c/d",), is_default=False)
    snap = _snap(default, mini, task_presets={"title": "mini"})
    assert resolve_task_preset(snap, "title") is mini


def test_resolve_task_preset_falls_back_to_default():
    default = LLMPreset(label="default", chain=("a/b",), is_default=True)
    assert resolve_task_preset(_snap(default), "compaction") is default


def test_resolve_preset_broken_ref_raises():
    snap = LLMSnapshot(
        providers={},  # no providers → every ref is broken
        presets=(LLMPreset(label="default", chain=("ghost/x",), is_default=True),),
        task_presets={},
    )
    with pytest.raises(BrokenPresetError) as exc:
        resolve_preset(snap, None)
    assert "ghost/x" in exc.value.missing_refs
