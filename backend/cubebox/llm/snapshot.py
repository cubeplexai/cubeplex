"""LLMSnapshot — per-request frozen view of LLM configuration.

A snapshot is loaded once per request via load_llm_snapshot(). Resolver
and builder modules take a snapshot as input and never read DB or
cubebox.config themselves.

Immutability is enforced at the type level: fields are typed as Mapping
(read-only) so mypy strict rejects mutation. The underlying objects are
dicts; this contract is type-system enforcement, not runtime.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from cubebox.llm.config import ProviderConfig


@dataclass(frozen=True)
class LLMPreset:
    label: str
    chain: tuple[str, ...]
    is_default: bool


@dataclass(frozen=True)
class LLMSnapshot:
    providers: Mapping[str, ProviderConfig]
    presets: tuple[LLMPreset, ...]
    task_presets: Mapping[str, str]
