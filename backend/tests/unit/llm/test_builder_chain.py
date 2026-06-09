"""builder.build_chain_model — chain length 1 returns BoundModel; >1 wraps FallbackBoundModel."""

from cubebox.llm.builder import build_chain_model
from cubebox.llm.config import ModelConfig, ProviderConfig
from cubebox.llm.snapshot import LLMPreset, LLMSnapshot


def _snap() -> LLMSnapshot:
    return LLMSnapshot(
        providers={
            "acme": ProviderConfig(
                api="openai-completions",
                base_url="https://x",
                api_key="k",
                models=[ModelConfig(id="m1", name="m1", context_window=128000, max_tokens=32000)],
            )
        },
        presets=(LLMPreset(label="default", chain=("acme/m1",), is_default=True),),
        task_presets={},
    )


def test_chain_length_1_returns_boundmodel():
    snap = _snap()
    preset = snap.presets[0]
    from cubepi.providers.base import BoundModel

    bm = build_chain_model(snap, preset)
    assert isinstance(bm, BoundModel)


def test_chain_length_2_returns_fallback_bound_model():
    snap = LLMSnapshot(
        providers={
            "acme": ProviderConfig(
                api="openai-completions",
                base_url="https://x",
                api_key="k",
                models=[
                    ModelConfig(id="m1", name="m1", context_window=128000, max_tokens=32000),
                    ModelConfig(id="m2", name="m2", context_window=128000, max_tokens=32000),
                ],
            )
        },
        presets=(LLMPreset(label="d", chain=("acme/m1", "acme/m2"), is_default=True),),
        task_presets={},
    )
    preset = snap.presets[0]
    from cubepi.providers.fallback import FallbackBoundModel

    bm = build_chain_model(snap, preset)
    assert isinstance(bm, FallbackBoundModel)
    assert len(bm.chain) == 2


def test_chain_passes_on_failover_callback():
    snap = LLMSnapshot(
        providers={
            "acme": ProviderConfig(
                api="openai-completions",
                base_url="https://x",
                api_key="k",
                models=[
                    ModelConfig(id="m1", name="m1", context_window=128000, max_tokens=32000),
                    ModelConfig(id="m2", name="m2", context_window=128000, max_tokens=32000),
                ],
            )
        },
        presets=(LLMPreset(label="d", chain=("acme/m1", "acme/m2"), is_default=True),),
        task_presets={},
    )
    calls: list = []

    async def cb(failed, nxt, err):
        calls.append((failed, nxt, err))

    bm = build_chain_model(snap, snap.presets[0], on_failover=cb)
    assert bm.on_failover is cb
