"""LLM Factory

Creates LLM instances based on configuration.
Supports OpenAI-compatible models with reasoning content via Chat Completions API.
"""

import logging
from typing import Any

from langchain_openai import ChatOpenAI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.config import config
from cubebox.credentials.encryption import EncryptionBackend
from cubebox.llm.config import LLMConfig, ModelConfig, ProviderConfig
from cubebox.llm.openai_compatible import ChatOpenAICompatible

logger = logging.getLogger(__name__)


class LLMFactory:
    """Factory for creating LLM instances from config.yaml (fallback) and DB (primary)."""

    def __init__(
        self,
        llm_config: LLMConfig | None = None,
        session: AsyncSession | None = None,
        org_id: str | None = None,
        encryption_backend: EncryptionBackend | None = None,
    ):
        """
        Initialize LLM factory.

        Args:
            llm_config: LLM configuration. If None, loads from global config.
            session: Optional async DB session for DB-driven config loading.
            org_id: Optional org ID for DB-driven config loading.
            encryption_backend: Optional vault backend; required to decrypt
                provider api_key credentials when loading from DB.
        """
        self._session = session
        self._org_id = org_id
        self._backend = encryption_backend
        if llm_config is None:
            # Load from global config
            llm_config = LLMConfig(**config.llm)
        self.llm_config = llm_config

    # ── DB-driven config loading ────────────────────────────────────

    async def _load_db_provider_configs(self) -> tuple[dict[str, dict[str, Any]], set[str]]:
        """Load enabled provider configs from DB.

        Returns (dict[name, config_dict], set of ALL provider names in DB).
        """
        if not self._session or not self._org_id:
            return {}, set()

        from cubebox.models import Credential
        from cubebox.models.org_provider_override import OrgProviderOverride as DBO
        from cubebox.models.provider import Model as DBM
        from cubebox.models.provider import Provider as DBP

        # Load providers visible to this org (handles org_provider_overrides)
        stmt = (
            select(DBP)
            .outerjoin(
                DBO,
                (DBP.id == DBO.provider_id) & (DBO.org_id == self._org_id),  # type: ignore[arg-type]
            )
            .where(
                (DBP.org_id == None) | (DBP.org_id == self._org_id),  # type: ignore[arg-type]  # noqa: E711
            )
            .where(
                func.coalesce(DBO.enabled, DBP.enabled, True),
            )
        )
        result = await self._session.execute(stmt)
        providers = result.scalars().all()

        db_configs: dict[str, dict[str, Any]] = {}
        for p in providers:
            # Load enabled models for this provider
            models_result = await self._session.execute(
                select(DBM).where(
                    DBM.provider_id == p.id,  # type: ignore[arg-type]
                    DBM.enabled,  # type: ignore[arg-type]
                ),
            )
            db_models = models_result.scalars().all()

            api_key: str | None = None
            if p.credential_id and self._backend is not None:
                cred_row = await self._session.get(Credential, p.credential_id)
                if cred_row is None:
                    logger.warning(
                        "Provider %s references missing credential %s",
                        p.name,
                        p.credential_id,
                    )
                elif cred_row.kind == "provider_api_key":
                    try:
                        api_key = (await self._backend.decrypt(cred_row.value_encrypted)).decode(
                            "utf-8"
                        )
                    except Exception:
                        logger.warning(
                            "Failed to decrypt credential %s for provider %s; "
                            "skipping api_key. Likely cause: encryption key "
                            "mismatch (rotate CUBEBOX_AUTH__VAULT_KEY back or "
                            "rotate the credential).",
                            p.credential_id,
                            p.name,
                        )

            db_configs[p.name] = {
                "base_url": p.base_url,
                "api_key": api_key,
                "api": "openai-completions",
                "extra_body": p.extra_body,
                "extra_headers": p.extra_headers,
                "models": [
                    {
                        "id": m.model_id,
                        "name": m.display_name,
                        "reasoning": m.reasoning,
                        "input": m.input_modalities,
                        "cost": {
                            "input": m.cost_input,
                            "output": m.cost_output,
                            "cache_read": m.cost_cache_read,
                            "cache_write": m.cost_cache_write,
                        },
                        "contextWindow": m.context_window,
                        "maxTokens": m.max_tokens,
                        "extra_body": m.extra_body,
                        "extra_headers": m.extra_headers,
                    }
                    for m in db_models
                ],
            }
        # Also get ALL provider names (including disabled) so _build_merged_config
        # can skip config.yaml providers that exist in DB regardless of enabled state.
        all_names_stmt = select(DBP).where(
            (DBP.org_id == None) | (DBP.org_id == self._org_id),  # type: ignore[arg-type]  # noqa: E711
        )
        all_names_result = await self._session.execute(all_names_stmt)
        db_names = {p.name for p in all_names_result.scalars().all()}
        return db_configs, db_names

    def _build_merged_config(
        self, db_configs: dict[str, dict[str, Any]], db_names: set[str]
    ) -> LLMConfig:
        """Merge DB configs with config.yaml fallback.

        CRITICAL: Only config-fallback providers that do NOT exist in DB at all.
        Once a provider is seeded into DB, its visibility is governed by DB +
        OrgProviderOverride, and config.yaml must NOT reintroduce it.
        """
        config_providers = dict(self.llm_config.providers)
        merged: dict[str, ProviderConfig] = {}
        for name, cfg in config_providers.items():
            if name not in db_names:  # Skip if provider exists in DB (even disabled)
                merged[name] = cfg  # Only use config when provider not in DB
        for name, db_cfg in db_configs.items():
            merged[name] = ProviderConfig(**db_cfg)  # DB always overrides
        return LLMConfig(
            default_model=self.llm_config.default_model,
            fallback_models=self.llm_config.fallback_models,
            providers=merged,
        )

    async def _get_org_default_model(self) -> str | None:
        if not self._session or not self._org_id:
            return None
        from cubebox.models.org_settings import OrgSettings as DBS

        stmt = select(DBS).where(
            DBS.org_id == self._org_id,  # type: ignore[arg-type]
            DBS.key == "default_model",  # type: ignore[arg-type]
        )
        result = await self._session.execute(stmt)
        setting = result.scalar_one_or_none()
        if setting and setting.value.get("model_ref"):
            return str(setting.value["model_ref"])
        return None

    async def _get_org_fallback_models(self) -> list[str]:
        if not self._session or not self._org_id:
            return []
        from cubebox.models.org_settings import OrgSettings as DBS

        stmt = select(DBS).where(
            DBS.org_id == self._org_id,  # type: ignore[arg-type]
            DBS.key == "fallback_models",  # type: ignore[arg-type]
        )
        result = await self._session.execute(stmt)
        setting = result.scalar_one_or_none()
        if setting and setting.value.get("models"):
            return list(setting.value["models"])
        return []

    @staticmethod
    def _parse_model_ref(model_ref: str) -> tuple[str, str]:
        """
        Parse a model reference in "provider/model-id" format.

        Args:
            model_ref: Model reference string

        Returns:
            Tuple of (provider_name, model_id)

        Raises:
            ValueError: If format is invalid
        """
        parts = model_ref.split("/", 1)
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError(f"Invalid model format: '{model_ref}'. Expected 'provider/model-id'")
        return parts[0], parts[1]

    async def get_default_model(self) -> tuple[str, str]:
        """
        Parse the default_model, checking org override first.

        Returns:
            Tuple of (provider_name, model_id)

        Raises:
            ValueError: If default_model is not set or has invalid format
        """
        model_ref = await self._get_org_default_model()
        if not model_ref:
            model_ref = self.llm_config.default_model
        if not model_ref:
            raise ValueError("No default_model configured")
        return self._parse_model_ref(model_ref)

    async def create_default(self, **kwargs: Any) -> Any:
        """
        Create an LLM instance using the configured default_model,
        with fallback models chained via with_fallbacks() if configured.

        With session+org_id: loads from DB, merges with config fallback.
        Without session: pure config.yaml (startup/CI compatibility).

        Args:
            **kwargs: Additional kwargs passed to create()

        Returns:
            LLM instance (with fallbacks if configured)
        """
        if self._session and self._org_id:
            db_cfgs, db_names = await self._load_db_provider_configs()
            self.llm_config = self._build_merged_config(db_cfgs, db_names)

        provider_name, model_id = await self.get_default_model()
        llm = self.create(model_id=model_id, provider_name=provider_name, **kwargs)

        fallback_refs = await self._get_org_fallback_models()
        if not fallback_refs:
            fallback_refs = self.llm_config.fallback_models
        if not fallback_refs:
            return llm

        fallbacks = []
        for model_ref in fallback_refs:
            try:
                fb_provider, fb_model_id = self._parse_model_ref(model_ref)
                fallbacks.append(
                    self.create(model_id=fb_model_id, provider_name=fb_provider, **kwargs),
                )
            except ValueError:
                logger.warning("Skipping invalid fallback model: '%s'", model_ref)

        if not fallbacks:
            return llm

        logger.info(
            "LLM fallback chain: %s -> %s",
            self.llm_config.default_model,
            list(fallback_refs),
        )
        return llm.with_fallbacks(fallbacks)

    def _find_model(
        self, model_id: str, provider_name: str | None = None
    ) -> tuple[str, ProviderConfig, ModelConfig]:
        """
        Find model configuration by model_id and optional provider_name.

        Args:
            model_id: Model ID to search for
            provider_name: Optional provider name to narrow search

        Returns:
            Tuple of (provider_name, provider_config, model_config)

        Raises:
            ValueError: If model not found or provider not found
        """
        if provider_name:
            # Search in specific provider
            provider_config = self.llm_config.providers.get(provider_name)
            if not provider_config:
                raise ValueError(f"Provider '{provider_name}' not found in config")

            for model in provider_config.models:
                if model.id == model_id:
                    return provider_name, provider_config, model

            raise ValueError(f"Model '{model_id}' not found in provider '{provider_name}'")

        # Search across all providers
        found_models = []
        for prov_name, prov_config in self.llm_config.providers.items():
            for model in prov_config.models:
                if model.id == model_id:
                    found_models.append((prov_name, prov_config, model))

        if not found_models:
            raise ValueError(f"Model '{model_id}' not found in any provider")

        if len(found_models) > 1:
            provider_names = [m[0] for m in found_models]
            logger.warning(
                "Model '%s' found in multiple providers: %s. Using first match: '%s'",
                model_id,
                provider_names,
                found_models[0][0],
            )

        return found_models[0]

    def create(
        self,
        model_id: str,
        provider_name: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        reasoning_config: dict[str, Any] | None = None,
        use_responses_api: bool = False,
        **kwargs: Any,
    ) -> Any:
        """
        Create an LLM instance based on model_id and optional provider_name.

        If only model_id is provided, searches across all providers.
        If model_id exists in multiple providers, logs a warning and uses the first match.

        For OpenAI official API with reasoning models (o1, o3, etc.):
        - Set reasoning_config to enable reasoning via Responses API
        - Example: reasoning_config={'effort': 'medium', 'summary': 'auto'}

        For OpenAI-compatible endpoints (DeepSeek, DouBao, Qwen, etc.):
        - Uses ChatOpenAICompatible to extract reasoning_content from Chat Completions
        - Automatically used for custom base_url endpoints

        Args:
            model_id: Model ID (e.g., 'doubao-seed-1.8')
            provider_name: Optional provider name (e.g., 'sensedeal-ai')
            temperature: Temperature parameter (default: 0.7)
            max_tokens: Override max tokens from model config
            reasoning_config: Reasoning config for OpenAI reasoning models
            use_responses_api: Use OpenAI Responses API
            **kwargs: Additional kwargs passed to LLM constructor

        Returns:
            LLM instance (ChatOpenAI or ChatOpenAICompatible)

        Raises:
            ValueError: If provider or model not found, or API type not supported
        """
        # Find model configuration
        provider_name, provider_config, model_config = self._find_model(model_id, provider_name)

        # Build kwargs for LLM initialization
        llm_kwargs: dict[str, Any] = {
            "model": model_config.id,
            "base_url": provider_config.base_url,
            "temperature": temperature,
        }
        if provider_config.api_key is not None:
            llm_kwargs["api_key"] = provider_config.api_key

        # Use provided max_tokens if set, otherwise use model's max_tokens
        final_max_tokens = max_tokens or model_config.max_tokens
        if final_max_tokens:
            llm_kwargs["max_tokens"] = final_max_tokens

        # Merge extra_body and extra_headers (model overrides provider)
        extra_body = {**provider_config.extra_body, **model_config.extra_body}
        extra_headers = {
            **provider_config.extra_headers,
            **model_config.extra_headers,
        }

        if extra_body:
            llm_kwargs["model_kwargs"] = {"extra_body": extra_body}
        if extra_headers:
            llm_kwargs["default_headers"] = extra_headers

        # Merge additional kwargs
        llm_kwargs.update(kwargs)

        # Handle different API types
        if provider_config.api == "openai-completions":
            # Ensure streamed responses include token usage so CostMiddleware can
            # populate billing rows. ChatOpenAI defaults stream_usage to False,
            # which causes usage_metadata to be missing on streamed AIMessages
            # and every billing event ends up with zero tokens.
            llm_kwargs.setdefault("stream_usage", True)

            # Check if this is official OpenAI API
            is_official_openai = (
                not provider_config.base_url or "api.openai.com" in provider_config.base_url
            )

            # Build the llm instance
            if is_official_openai:
                # Official OpenAI API - use ChatOpenAI with Responses API for reasoning
                if reasoning_config:
                    llm_kwargs["reasoning"] = reasoning_config
                elif use_responses_api:
                    llm_kwargs["use_responses_api"] = True
                llm = ChatOpenAI(**llm_kwargs)
            else:
                # Custom OpenAI-compatible endpoint - use ChatOpenAICompatible
                # This supports reasoning_content extraction from Chat Completions API
                llm = ChatOpenAICompatible(**llm_kwargs)

            # Attach cubebox metadata for CostMiddleware to read
            llm._cubebox_provider = provider_name  # type: ignore[attr-defined]
            llm._cubebox_model_id = model_config.id  # type: ignore[attr-defined]
            llm._cubebox_model_cost = model_config.cost  # type: ignore[attr-defined]
            return llm

        if provider_config.api == "anthropic":
            # TODO: Implement Anthropic support
            raise NotImplementedError("Anthropic API not yet implemented")

        raise ValueError(f"Unsupported API type: {provider_config.api}")

    def get_model_config(self, provider_name: str, model_id: str) -> ModelConfig:
        """
        Get model configuration.

        Args:
            provider_name: Provider name
            model_id: Model ID

        Returns:
            ModelConfig instance

        Raises:
            ValueError: If provider or model not found
        """
        provider_config = self.llm_config.providers.get(provider_name)
        if not provider_config:
            raise ValueError(f"Provider '{provider_name}' not found in config")

        for model in provider_config.models:
            if model.id == model_id:
                return model

        raise ValueError(f"Model '{model_id}' not found in provider '{provider_name}'")

    def list_providers(self) -> list[str]:
        """List all available provider names."""
        return list(self.llm_config.providers.keys())

    def list_models(self, provider_name: str) -> list[str]:
        """
        List all model IDs for a provider.

        Args:
            provider_name: Provider name

        Returns:
            List of model IDs

        Raises:
            ValueError: If provider not found
        """
        provider_config = self.llm_config.providers.get(provider_name)
        if not provider_config:
            raise ValueError(f"Provider '{provider_name}' not found in config")

        return [model.id for model in provider_config.models]
