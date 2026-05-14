"""ProviderService -- CRUD, invariants, test connection, seed."""

from __future__ import annotations

import time

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.api.schemas.provider import (
    ModelCreate,
    ModelUpdate,
    OrgLLMSettingsOut,
    OrgLLMSettingsUpdate,
    ProviderCreate,
    ProviderTest,
    ProviderUpdate,
    TestResultOut,
)
from cubebox.credentials.exceptions import CredentialNotFound
from cubebox.models.org_provider_override import OrgProviderOverride
from cubebox.models.provider import Model, Provider
from cubebox.repositories.model import ModelRepository
from cubebox.repositories.org_provider_override import OrgProviderOverrideRepository
from cubebox.repositories.org_settings import OrgSettingsRepository
from cubebox.repositories.provider import ProviderRepository
from cubebox.services.credential import CredentialService

_PROVIDER_KEY_KIND = "provider_api_key"


class ProviderOAuthNotImplementedError(Exception):
    """Raised when auth_type=oauth is used in v1."""


class ProviderNameConflictError(Exception):
    """Raised when provider name is duplicate in same scope."""


class ProviderSystemReadonlyError(Exception):
    """Raised when trying to mutate a system provider."""


class ProviderOverrideNotApplicableError(Exception):
    """Raised when override is set on org-level provider."""


class ProviderNotFoundError(Exception):
    """Raised when provider is not found."""


class ModelNotFoundError(Exception):
    """Raised when model is not found."""


class ProviderService:
    def __init__(
        self,
        *,
        provider_repo: ProviderRepository,
        model_repo: ModelRepository,
        override_repo: OrgProviderOverrideRepository,
        org_settings_repo: OrgSettingsRepository,
        credential_service: CredentialService,
        session: AsyncSession,
        org_id: str,
        actor_user_id: str,
    ) -> None:
        self._providers = provider_repo
        self._models = model_repo
        self._overrides = override_repo
        self._org_settings = org_settings_repo
        self._credentials = credential_service
        self._session = session
        self.org_id = org_id
        self.actor_user_id = actor_user_id

    def _check_not_system(self, provider: Provider) -> None:
        if provider.org_id is None:
            raise ProviderSystemReadonlyError("System providers cannot be modified or deleted")

    def _check_oauth(self, auth_type: str | None) -> None:
        if auth_type == "oauth":
            raise ProviderOAuthNotImplementedError("OAuth authentication is not yet implemented")

    def _validate_auth_creds(self, auth_type: str, has_key: bool) -> None:
        """Enforce auth_type / api_key cross-invariants."""
        if auth_type in ("api_key", "bearer_token") and not has_key:
            raise ValueError(f"api_key is required for auth_type={auth_type}")
        if auth_type == "none" and has_key:
            raise ValueError("api_key must be empty for auth_type='none'")

    async def _resolve_api_key(self, provider: Provider) -> str | None:
        """Decrypt the provider's stored api key, or None if no credential is set."""
        if not provider.credential_id:
            return None
        try:
            return await self._credentials.get_decrypted(
                credential_id=provider.credential_id,
                requesting_kind=_PROVIDER_KEY_KIND,
            )
        except CredentialNotFound:
            return None

    # -- Provider CRUD -----------------------------------------------------------

    async def list_providers(self) -> list[Provider]:
        return await self._providers.list_visible()

    async def get_provider(self, provider_id: str) -> Provider:
        p = await self._providers.get(provider_id)
        if p is None:
            raise ProviderNotFoundError(f"Provider {provider_id} not found")
        return p

    async def create_provider(self, data: ProviderCreate) -> Provider:
        self._check_oauth(data.auth_type)
        self._validate_auth_creds(data.auth_type, bool(data.api_key))
        existing = await self._providers.get_by_name(data.name)
        if existing is not None:
            raise ProviderNameConflictError(f"Provider name '{data.name}' already exists")

        credential_id: str | None = None
        if data.auth_type != "none" and data.api_key:
            credential_id = await self._credentials.create(
                kind=_PROVIDER_KEY_KIND,
                name=data.name,
                plaintext=data.api_key,
            )

        p = Provider(
            org_id=self.org_id,
            name=data.name,
            provider_type=data.provider_type,
            base_url=data.base_url,
            auth_type=data.auth_type,
            credential_id=credential_id,
            logo_url=data.logo_url,
            extra_body=data.extra_body,
            extra_headers=data.extra_headers,
            created_by_user_id=self.actor_user_id,
        )
        return await self._providers.add(p)

    async def update_provider(self, provider_id: str, data: ProviderUpdate) -> Provider:
        p = await self.get_provider(provider_id)
        self._check_not_system(p)

        effective_auth = data.auth_type if data.auth_type is not None else p.auth_type
        will_have_key = (
            bool(data.api_key)
            if data.api_key is not None
            else (p.credential_id is not None and effective_auth != "none")
        )
        if data.auth_type is not None or data.api_key is not None:
            self._check_oauth(effective_auth)
            self._validate_auth_creds(effective_auth, will_have_key)

        if data.name is not None and data.name != p.name:
            existing = await self._providers.get_by_name(data.name)
            if existing is not None:
                raise ProviderNameConflictError(f"Provider name '{data.name}' already exists")
            if p.credential_id:
                await self._credentials.update(credential_id=p.credential_id, name=data.name)
            p.name = data.name

        for field in (
            "provider_type",
            "base_url",
            "auth_type",
            "logo_url",
            "extra_body",
            "extra_headers",
        ):
            val = getattr(data, field, None)
            if val is not None:
                setattr(p, field, val)

        if effective_auth == "none" and p.credential_id:
            old_cred = p.credential_id
            p.credential_id = None
            await self._providers.update(p)
            await self._credentials.delete(credential_id=old_cred)
        elif data.api_key is not None:
            if data.api_key:
                if p.credential_id:
                    await self._credentials.update(
                        credential_id=p.credential_id, plaintext=data.api_key
                    )
                else:
                    p.credential_id = await self._credentials.create(
                        kind=_PROVIDER_KEY_KIND,
                        name=p.name,
                        plaintext=data.api_key,
                    )
            elif p.credential_id:
                old_cred = p.credential_id
                p.credential_id = None
                await self._providers.update(p)
                await self._credentials.delete(credential_id=old_cred)

        if data.enabled is not None:
            p.enabled = data.enabled
        return await self._providers.update(p)

    async def delete_provider(self, provider_id: str) -> None:
        p = await self.get_provider(provider_id)
        self._check_not_system(p)
        cred_id = p.credential_id
        await self._models.delete_by_provider(provider_id)
        await self._overrides.delete(provider_id)
        await self._providers.delete(p)
        await self._session.commit()
        if cred_id:
            await self._credentials.delete(credential_id=cred_id)

    # -- Model CRUD -------------------------------------------------------------

    async def list_models(self, provider_id: str) -> list[Model]:
        await self.get_provider(provider_id)
        return await self._models.list_by_provider(provider_id)

    async def create_model(self, provider_id: str, data: ModelCreate) -> Model:
        provider = await self.get_provider(provider_id)
        self._check_not_system(provider)
        existing = await self._models.get_by_model_id(provider_id, data.model_id)
        if existing is not None:
            raise ValueError(f"Model '{data.model_id}' already exists in this provider")
        m = Model(
            org_id=provider.org_id,
            provider_id=provider_id,
            model_id=data.model_id,
            display_name=data.display_name,
            reasoning=data.reasoning,
            input_modalities=data.input_modalities,
            cost_input=data.cost_input,
            cost_output=data.cost_output,
            cost_cache_read=data.cost_cache_read,
            cost_cache_write=data.cost_cache_write,
            context_window=data.context_window,
            max_tokens=data.max_tokens,
            extra_body=data.extra_body,
            extra_headers=data.extra_headers,
        )
        return await self._models.add(m)

    async def update_model(self, provider_id: str, model_db_id: str, data: ModelUpdate) -> Model:
        provider = await self.get_provider(provider_id)
        self._check_not_system(provider)
        m = await self._models.get(model_db_id)
        if m is None or m.provider_id != provider_id:
            raise ModelNotFoundError(f"Model {model_db_id} not found")
        for field in (
            "display_name",
            "reasoning",
            "input_modalities",
            "cost_input",
            "cost_output",
            "cost_cache_read",
            "cost_cache_write",
            "context_window",
            "max_tokens",
            "extra_body",
            "extra_headers",
        ):
            val = getattr(data, field, None)
            if val is not None:
                setattr(m, field, val)
        if data.enabled is not None:
            m.enabled = data.enabled
        return await self._models.update(m)

    async def delete_model(self, provider_id: str, model_db_id: str) -> None:
        provider = await self.get_provider(provider_id)
        self._check_not_system(provider)
        m = await self._models.get(model_db_id)
        if m is None or m.provider_id != provider_id:
            raise ModelNotFoundError(f"Model {model_db_id} not found")
        await self._models.delete(m)

    # -- Test connection --------------------------------------------------------

    async def test_connection(self, data: ProviderTest) -> TestResultOut:
        start = time.monotonic()
        if data.provider_type not in ("openai_compat",):
            return TestResultOut(
                ok=False,
                error=f"Unsupported provider_type: {data.provider_type}",
                latency_ms=0,
            )
        try:
            if data.provider_type == "openai_compat":
                llm = ChatOpenAI(
                    base_url=data.base_url,
                    api_key=data.api_key or "placeholder",  # type: ignore[arg-type]
                    model="ping",
                    timeout=15,
                )
                await llm.ainvoke([HumanMessage(content="ping")])
            latency_ms = int((time.monotonic() - start) * 1000)
            return TestResultOut(ok=False, error="Unexpected success", latency_ms=latency_ms)
        except Exception as e:
            latency_ms = int((time.monotonic() - start) * 1000)
            error_str = str(e)
            if (
                "Connection refused" in error_str
                or "Name or service not known" in error_str
                or "getaddrinfo" in error_str.lower()
            ):
                return TestResultOut(ok=False, error=error_str, latency_ms=latency_ms)
            return TestResultOut(ok=True, error=None, latency_ms=latency_ms)

    async def test_model_connection(self, provider_id: str, model_id: str) -> TestResultOut:
        """Test reachability of a specific model on a provider using stored credentials."""
        start = time.monotonic()
        provider = await self.get_provider(provider_id)
        api_key = await self._resolve_api_key(provider)
        try:
            from langchain_core.language_models import BaseChatModel

            llm: BaseChatModel
            if provider.provider_type == "anthropic":
                from langchain_anthropic import ChatAnthropic

                llm = ChatAnthropic(
                    model=model_id,  # type: ignore[call-arg]
                    base_url=provider.base_url,
                    api_key=api_key or "placeholder",  # type: ignore[arg-type]
                    max_tokens=32,
                    timeout=15,
                )
            elif provider.provider_type == "openai_compat":
                llm = ChatOpenAI(
                    base_url=provider.base_url,
                    api_key=api_key or "placeholder",  # type: ignore[arg-type]
                    model=model_id,
                    timeout=15,
                )
            else:
                return TestResultOut(
                    ok=False,
                    error=f"Unsupported provider_type: {provider.provider_type}",
                    latency_ms=0,
                )
            await llm.ainvoke([HumanMessage(content="ping")])
            latency_ms = int((time.monotonic() - start) * 1000)
            return TestResultOut(ok=True, error=None, latency_ms=latency_ms)
        except Exception as e:
            latency_ms = int((time.monotonic() - start) * 1000)
            return TestResultOut(ok=False, error=str(e), latency_ms=latency_ms)

    # -- Org overrides ----------------------------------------------------------

    async def get_override(self, provider_id: str) -> OrgProviderOverride | None:
        p = await self.get_provider(provider_id)
        if p.org_id is not None:
            return None
        return await self._overrides.get(provider_id)

    async def set_override(self, provider_id: str, enabled: bool) -> OrgProviderOverride:
        p = await self.get_provider(provider_id)
        if p.org_id is not None:
            raise ProviderOverrideNotApplicableError("Override only applies to system providers")
        return await self._overrides.set(provider_id, enabled)

    # -- Org settings -----------------------------------------------------------

    async def get_llm_settings(self) -> OrgLLMSettingsOut:
        default = await self._org_settings.get("default_model")
        fallback = await self._org_settings.get("fallback_models")
        return OrgLLMSettingsOut(
            default_model=default.value.get("model_ref") if default else None,
            fallback_models=fallback.value.get("models", []) if fallback else [],
        )

    async def update_llm_settings(self, data: OrgLLMSettingsUpdate) -> OrgLLMSettingsOut:
        if data.default_model is not None:
            await self._validate_model_ref(data.default_model)
            await self._org_settings.set("default_model", {"model_ref": data.default_model})
        if data.fallback_models is not None:
            for ref in data.fallback_models:
                await self._validate_model_ref(ref)
            await self._org_settings.set("fallback_models", {"models": data.fallback_models})
        return await self.get_llm_settings()

    async def _validate_model_ref(self, model_ref: str) -> None:
        """Verify a provider/model-id reference points to a visible, enabled model."""
        parts = model_ref.split("/", 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid model ref format: '{model_ref}'")
        provider_name, model_id = parts
        provider = await self._providers.get_by_name(provider_name)
        if provider is None:
            raise ValueError(f"Provider '{provider_name}' not found")
        if provider.org_id is None:
            override = await self._overrides.get(provider.id)
            if override and not override.enabled:
                raise ValueError(f"Provider '{provider_name}' is disabled by org")
        model = await self._models.get_by_model_id(provider.id, model_id)
        if model is None:
            raise ValueError(f"Model '{model_id}' not found in provider '{provider_name}'")
        if not model.enabled:
            raise ValueError(f"Model '{model_id}' is disabled")
