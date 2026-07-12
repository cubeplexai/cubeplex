"""Aggregated input modality capability across primary + fallback models."""

from __future__ import annotations

from cubeplex.llm.config import LLMConfig, ModelConfig


class LLMCapabilities:
    """Read input modalities from primary + fallback models in LLMConfig."""

    def __init__(self, llm_config: LLMConfig) -> None:
        self._cfg = llm_config

    def _resolve(self, model_ref: str) -> ModelConfig | None:
        """`provider/model_id` reference -> ModelConfig (or None if not found)."""
        if "/" not in model_ref:
            return None
        provider_name, model_id = model_ref.split("/", 1)
        provider = self._cfg.providers.get(provider_name)
        if provider is None:
            return None
        for m in provider.models:
            if m.id == model_id:
                return m
        return None

    def combined_input_modalities(self) -> set[str]:
        """Union of supported input types across the active model + its fallbacks."""
        modalities: set[str] = set()
        for ref in [self._cfg.default_model, *self._cfg.fallback_models]:
            if ref is not None:
                m = self._resolve(ref)
                if m is not None:
                    modalities.update(m.input)
        return modalities

    def supports_image(self) -> bool:
        return "image" in self.combined_input_modalities()
