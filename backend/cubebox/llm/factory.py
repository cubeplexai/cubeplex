"""LLM Factory

Creates cubepi.Provider instances for the agent runtime.
All langchain code paths were removed after the cubepi migration (M6 + follow-up).

Surface:
- ``resolve_default_provider_and_config`` — resolves the active provider/model
- ``build_cubepi_provider`` — constructs a ``cubepi.Provider`` for the agent loop
"""

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cubepi.providers.anthropic import CacheMarkerPolicy

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.config import config
from cubebox.credentials.encryption import EncryptionBackend
from cubebox.llm.config import LLMConfig, ModelConfig, ProviderConfig

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

        Returns (dict[slug, config_dict], set of ALL provider slugs in DB).
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

            db_configs[p.slug] = {
                "base_url": p.base_url,
                "api_key": api_key,
                "api": p.provider_type,
                "extra_body": p.extra_body,
                "extra_headers": p.extra_headers,
                "capability": p.capability or {},
                "model_capability_overrides": p.model_capability_overrides or {},
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
        # Also get ALL provider slugs (including disabled) so _build_merged_config
        # can skip config.yaml providers that exist in DB regardless of enabled state.
        all_rows_stmt = select(DBP).where(
            (DBP.org_id == None) | (DBP.org_id == self._org_id),  # type: ignore[arg-type]  # noqa: E711
        )
        all_rows = (await self._session.execute(all_rows_stmt)).scalars().all()
        db_slugs = {p.slug for p in all_rows}
        return db_configs, db_slugs

    def _build_merged_config(
        self, db_configs: dict[str, dict[str, Any]], db_slugs: set[str]
    ) -> LLMConfig:
        """Merge DB configs with config.yaml fallback.

        CRITICAL: Only config-fallback providers that do NOT exist in DB at all.
        Once a provider is seeded into DB, its visibility is governed by DB +
        OrgProviderOverride, and config.yaml must NOT reintroduce it.

        Both DB providers (keyed by slug) and config.yaml providers (keyed by
        slugify(name)) are merged into a single slug-keyed dict.
        """
        from cubebox.utils.slug import slugify

        config_providers = dict(self.llm_config.providers)
        # Normalise slugs for the exclusion check so config-yaml providers whose
        # slug matches a DB provider are correctly excluded.
        db_slugs_lower = {s.lower() for s in db_slugs}
        merged: dict[str, ProviderConfig] = {}
        for name, cfg in config_providers.items():
            slug = slugify(name)
            if slug.lower() not in db_slugs_lower:  # Skip if provider exists in DB
                merged[slug] = cfg  # Only use config when provider not in DB
        for slug, db_cfg in db_configs.items():
            merged[slug] = ProviderConfig(**db_cfg)  # DB always overrides

        # The merged providers map is slug-keyed, but config.yaml's default/fallback
        # refs use the raw provider key (e.g. "Acme AI/model"). Normalise the
        # provider segment of these config-level refs to its slug so they resolve
        # against the slug-keyed map. (OrgSettings refs are read separately and were
        # already migrated to slug — they are not touched here.)
        def _slug_ref(ref: str) -> str:
            head, sep, tail = ref.partition("/")
            return f"{slugify(head)}{sep}{tail}" if sep else ref

        default_model = self.llm_config.default_model
        return LLMConfig(
            default_model=_slug_ref(default_model) if default_model else default_model,
            fallback_models=[_slug_ref(m) for m in self.llm_config.fallback_models],
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
        Parse a model reference in "slug/model-id" format.

        Args:
            model_ref: Model reference string

        Returns:
            Tuple of (slug, model_id)

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
            Tuple of (slug, model_id)

        Raises:
            ValueError: If default_model is not set or has invalid format
        """
        model_ref = await self._get_org_default_model()
        if not model_ref:
            model_ref = self.llm_config.default_model
        if not model_ref:
            raise ValueError("No default_model configured")
        return self._parse_model_ref(model_ref)

    async def get_default_model_config(self) -> ModelConfig:
        """Resolve the default model's ModelConfig, merging DB configs if available."""
        if self._session and self._org_id:
            db_cfgs, db_slugs = await self._load_db_provider_configs()
            self.llm_config = self._build_merged_config(db_cfgs, db_slugs)
        slug, model_id = await self.get_default_model()
        return self.get_model_config(slug, model_id)

    async def resolve_default_provider_and_config(
        self,
    ) -> tuple[str, str, ProviderConfig]:
        """Resolve the default provider name, model id, and ProviderConfig.

        Merges DB-stored provider overrides (when session + org_id are present)
        with the config-file providers, then parses the effective default_model
        reference.

        Returns:
            Tuple of (slug, model_id, ProviderConfig)

        Raises:
            ValueError: If default_model is unset, has invalid format, or the
                provider is not found after merging.
        """
        if self._session and self._org_id:
            db_cfgs, db_slugs = await self._load_db_provider_configs()
            self.llm_config = self._build_merged_config(db_cfgs, db_slugs)

        slug, model_id = await self.get_default_model()
        provider_config = self.llm_config.providers.get(slug)
        if provider_config is None:
            raise ValueError(f"Default provider '{slug}' not found in merged config")
        return slug, model_id, provider_config

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

    def build_cubepi_provider(
        self,
        provider_config: ProviderConfig,
        *,
        provider_name: str,
        cache_policy: "CacheMarkerPolicy | None" = None,
    ) -> Any:
        """Build a cubepi.Provider instance from a ProviderConfig.

        Routes by ``provider_config.api`` (the wire-api literal):

        - ``"anthropic-messages"`` → AnthropicProvider
        - ``"openai-completions"`` → cubepi OpenAIProvider
        - ``"openai-responses"``   → cubepi OpenAIResponsesProvider

        ``provider_name`` is the logical key under ``llm_config.providers`` and is
        stamped onto the returned provider as ``provider_id``, which then propagates to
        ``BoundModel.spec.provider_id`` for cost tracking and tracing.

        Capability quirks stored as JSON on the provider (``capability`` +
        ``model_capability_overrides``) are converted to typed
        ``CapabilityDescriptor`` objects and forwarded to the cubepi provider.
        Empty capability ⇒ ``None`` ⇒ legacy byte-identical behavior.

        ``cache_policy`` (Anthropic only): forwarded to AnthropicProvider.
        When ``None``, AnthropicProvider defaults to DefaultCacheMarkerPolicy.

        Raises:
            ValueError: If ``provider_config.api`` is not a recognised value.
        """
        from cubepi.providers.capability import CapabilityDescriptor

        api = provider_config.api

        cap_dict = provider_config.capability or {}
        capability = CapabilityDescriptor.model_validate(cap_dict) if cap_dict else None

        overrides_raw = provider_config.model_capability_overrides or {}
        model_capability_overrides: dict[str, CapabilityDescriptor] | None = {
            mid: CapabilityDescriptor.model_validate(d) for mid, d in overrides_raw.items()
        } or None

        if api == "anthropic-messages":
            from cubepi.providers.anthropic import AnthropicProvider

            return AnthropicProvider(
                provider_id=provider_name,
                api_key=provider_config.api_key,
                base_url=provider_config.base_url or None,
                cache_policy=cache_policy,
                capability=capability,
                model_capability_overrides=model_capability_overrides,
            )

        if api == "openai-completions":
            from cubepi.providers.openai import OpenAIProvider

            return OpenAIProvider(
                provider_id=provider_name,
                api_key=provider_config.api_key,
                base_url=provider_config.base_url,
                extra_body=provider_config.extra_body or None,
                extra_headers=provider_config.extra_headers or None,
                capability=capability,
                model_capability_overrides=model_capability_overrides,
            )

        if api == "openai-responses":
            from cubepi.providers.openai_responses import OpenAIResponsesProvider

            return OpenAIResponsesProvider(
                provider_id=provider_name,
                api_key=provider_config.api_key,
                base_url=provider_config.base_url,
                capability=capability,
                model_capability_overrides=model_capability_overrides,
            )

        raise ValueError(f"unsupported api for cubepi provider: {api!r}")

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
