"""LLMSnapshot — per-request frozen view of LLM configuration.

A snapshot is loaded once per request via load_llm_snapshot(). Resolver
and builder modules take a snapshot as input and never read DB or
cubebox.config themselves.
"""

from dataclasses import dataclass

from cubebox.llm.config import ProviderConfig


@dataclass(frozen=True)
class LLMPreset:
    label: str
    chain: tuple[str, ...]
    is_default: bool


@dataclass(frozen=True)
class LLMSnapshot:
    providers: dict[str, ProviderConfig]
    presets: tuple[LLMPreset, ...]
    task_presets: dict[str, str]
