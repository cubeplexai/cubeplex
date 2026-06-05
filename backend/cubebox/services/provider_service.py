"""ProviderService -- CRUD, invariants, two-phase test/liveness probe, seed."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.api.schemas.provider import (
    ModelCreate,
    ModelUpdate,
    OrgLLMSettingsOut,
    OrgLLMSettingsUpdate,
    ProviderCreate,
    ProviderLivenessRequest,
    ProviderTestRequest,
    ProviderUpdate,
)
from cubebox.credentials.exceptions import CredentialNotFound
from cubebox.llm.config import ProviderConfig
from cubebox.llm.factory import LLMFactory
from cubebox.llm.readiness import capability_fingerprint
from cubebox.models.org_provider_override import OrgProviderOverride
from cubebox.models.provider import Model, Provider
from cubebox.repositories.model import ModelRepository
from cubebox.repositories.org_provider_override import OrgProviderOverrideRepository
from cubebox.repositories.org_settings import OrgSettingsRepository
from cubebox.repositories.provider import ProviderRepository
from cubebox.services import provider_probe
from cubebox.services.credential import CredentialService
from cubebox.services.provider_probe import ProbeResult, ProbeStep
from cubebox.utils.slug import slugify

_PROVIDER_KEY_KIND = "provider_api_key"

_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# ProbeResult.overall → the model's persisted last_test_status.
_OVERALL_TO_STATUS: dict[str, str] = {
    "pass": "ok",
    "warn": "warn",
    "fail": "fail",
    "unavailable": "unavailable",
}


def _sse(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


class ProviderOAuthNotImplementedError(Exception):
    """Raised when auth_type=oauth is used in v1."""


class ProviderNameConflictError(Exception):
    """Raised when provider name is duplicate in same scope."""


class ProviderSlugConflictError(Exception):
    """Raised when a provider slug already exists in the org."""


class InvalidProviderSlugError(Exception):
    """Raised when an explicitly-provided slug is malformed."""


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

    async def _resolve_slug(self, name: str, explicit: str | None) -> str:
        if explicit is not None:
            if not _SLUG_RE.match(explicit) or len(explicit) > 64:
                raise InvalidProviderSlugError(
                    "slug must match ^[a-z0-9]+(-[a-z0-9]+)*$ and be <= 64 chars"
                )
            if await self._providers.get_by_slug(explicit) is not None:
                raise ProviderSlugConflictError(f"Provider slug '{explicit}' already exists")
            return explicit
        base = slugify(name)
        n = 1
        while True:
            suffix = "" if n == 1 else f"-{n}"
            candidate = base[: 64 - len(suffix)] + suffix  # always fits the 64-char column
            if await self._providers.get_by_slug(candidate) is None:
                return candidate
            n += 1

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
        """Decrypt the provider's stored api key, or None if no credential is set.

        A system provider (org_id=NULL) carries a system credential the org-scoped
        credential service can't see, so read it with system scope — otherwise
        testing a system provider runs with a placeholder key and 401s.
        """
        if not provider.credential_id:
            return None
        try:
            if provider.org_id is None:
                return await self._credentials.get_decrypted_system(
                    credential_id=provider.credential_id,
                    requesting_kind=_PROVIDER_KEY_KIND,
                )
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

        slug = await self._resolve_slug(data.name, data.slug)

        # Backfill the capability snapshot from the catalog when the client sent a
        # `preset_slug` (preset_key) but no capability — the presets API no longer
        # exposes capability (resolved server-side). Mirrors the seeder.
        capability = data.capability
        if data.preset_slug and not capability:
            try:
                from cubebox.llm.catalog import load_catalog

                capability = (
                    load_catalog().resolve(data.preset_slug).capability.model_dump(mode="json")
                )
            except Exception:
                capability = data.capability

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
            slug=slug,
            provider_type=data.provider_type,
            base_url=data.base_url,
            auth_type=data.auth_type,
            credential_id=credential_id,
            logo_url=data.logo_url,
            extra_body=data.extra_body,
            extra_headers=data.extra_headers,
            preset_slug=data.preset_slug,
            capability=capability,
            model_capability_overrides=data.model_capability_overrides,
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
            "preset_slug",
            "capability",
            "model_capability_overrides",
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
            enabled=data.enabled,
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

    # -- Two-phase test / liveness probe ----------------------------------------

    def _provider_factory_from_config(
        self, cfg: ProviderConfig, *, provider_name: str
    ) -> Callable[[], Any]:
        """Zero-arg callable that builds a fresh cubepi provider for the probe.

        The probe orchestrators call the factory each phase, so each invocation
        must yield an independent provider instance.
        """

        def factory() -> Any:
            return LLMFactory().build_cubepi_provider(
                cfg, provider_name=provider_name, cache_policy=None
            )

        return factory

    def _resolve_capability(self, cfg: ProviderConfig, model_id: str, *, provider_name: str) -> Any:
        """Effective CapabilityDescriptor for ``model_id`` (override > base).

        Built off a throwaway cubepi provider so we reuse the exact same merge
        logic the runtime uses (``provider._resolve_capability``), avoiding a
        second copy of the override-precedence rule.
        """
        provider = LLMFactory().build_cubepi_provider(
            cfg, provider_name=provider_name, cache_policy=None
        )
        return provider._resolve_capability(model_id)

    @staticmethod
    def _config_from_request(req: ProviderLivenessRequest) -> ProviderConfig:
        return ProviderConfig(
            api=req.api,
            api_key=req.api_key or "placeholder",
            base_url=req.base_url or "",
            capability=req.capability or {},
            model_capability_overrides=req.model_capability_overrides or {},
            models=[],
        )

    async def _config_from_provider(self, provider: Provider) -> ProviderConfig:
        api_key = await self._resolve_api_key(provider)
        return ProviderConfig(
            api=provider.provider_type,
            api_key=api_key or "placeholder",
            base_url=provider.base_url or "",
            capability=provider.capability or {},
            model_capability_overrides=provider.model_capability_overrides or {},
            models=[],
        )

    async def _persist_provider_liveness(self, provider: Provider, step: ProbeStep) -> None:
        """Write the liveness verdict onto the provider row.

        Test status is observed metadata (not config), so we mutate + commit
        directly via the repo's ``update`` — which does NOT enforce the
        system-readonly guard — instead of going through ``_check_not_system``.
        """
        provider.last_liveness_at = datetime.now(UTC)
        provider.last_liveness_status = provider_probe.liveness_status_for(step)
        provider.last_liveness_summary = step.model_dump(mode="json")
        await self._providers.update(provider)

    async def _persist_model_test(
        self, model: Model, result: ProbeResult, fingerprint: str
    ) -> None:
        """Write the per-model probe verdict + capability fingerprint.

        The fingerprint is REQUIRED: Task 5 readiness compares it against the
        provider's current capability to flag a `stale` model after a capability
        edit. Without it `stale` never fires.
        """
        summary = result.model_dump(mode="json")
        summary["capability_fingerprint"] = fingerprint
        model.last_test_at = datetime.now(UTC)
        model.last_test_status = _OVERALL_TO_STATUS[result.overall]
        model.last_test_summary = summary
        await self._models.update(model)

    async def run_liveness_dryrun(self, req: ProviderLivenessRequest) -> ProbeStep:
        """Pre-save liveness — transient provider, no DB write (spec §4.3)."""
        cfg = self._config_from_request(req)
        return await provider_probe.run_liveness(
            provider_factory=self._provider_factory_from_config(cfg, provider_name=req.api),
            model_id=req.model_id,
        )

    async def run_liveness_saved(self, provider_id: str, model_id: str) -> ProbeStep:
        """Re-check a saved provider's liveness and persist the result."""
        provider = await self.get_provider(provider_id)
        cfg = await self._config_from_provider(provider)
        step = await provider_probe.run_liveness(
            provider_factory=self._provider_factory_from_config(cfg, provider_name=provider.slug),
            model_id=model_id,
        )
        await self._persist_provider_liveness(provider, step)
        return step

    async def run_test_dryrun(self, req: ProviderTestRequest) -> ProbeResult:
        """Pre-save full probe — liveness then per-model capability. No DB write."""
        cfg = self._config_from_request(req)
        factory = self._provider_factory_from_config(cfg, provider_name=req.api)
        liveness = await provider_probe.run_liveness(
            provider_factory=factory, model_id=req.model_id
        )
        if liveness.status != "pass":
            return ProbeResult(overall="fail", blocking_failed=True, steps=[liveness])
        capability = self._resolve_capability(cfg, req.model_id, provider_name=req.api)
        model_result = await provider_probe.run_model_probe(
            provider_factory=factory, model_id=req.model_id, capability=capability
        )
        return ProbeResult(
            overall=model_result.overall,
            blocking_failed=model_result.blocking_failed,
            steps=[liveness, *model_result.steps],
        )

    async def run_model_test_saved(self, provider_id: str, model_db_id: str) -> ProbeResult:
        """Saved single-model test: liveness (persisted) then capability (persisted)."""
        provider = await self.get_provider(provider_id)
        model = await self._models.get(model_db_id)
        if model is None or model.provider_id != provider_id:
            raise ModelNotFoundError(f"Model {model_db_id} not found")
        cfg = await self._config_from_provider(provider)
        factory = self._provider_factory_from_config(cfg, provider_name=provider.slug)
        liveness = await provider_probe.run_liveness(
            provider_factory=factory, model_id=model.model_id
        )
        await self._persist_provider_liveness(provider, liveness)
        if liveness.status != "pass":
            return ProbeResult(overall="fail", blocking_failed=True, steps=[liveness])
        capability = self._resolve_capability(cfg, model.model_id, provider_name=provider.slug)
        model_result = await provider_probe.run_model_probe(
            provider_factory=factory, model_id=model.model_id, capability=capability
        )
        fingerprint = capability_fingerprint(
            provider.capability or {}, provider.model_capability_overrides or {}
        )
        await self._persist_model_test(model, model_result, fingerprint)
        return ProbeResult(
            overall=model_result.overall,
            blocking_failed=model_result.blocking_failed,
            steps=[liveness, *model_result.steps],
        )

    async def run_all_models_test_saved(self, provider_id: str) -> list[ProbeResult]:
        """Saved all-models test: one liveness (persisted), then each enabled model."""
        provider = await self.get_provider(provider_id)
        models = await self._models.list_by_provider(provider_id)
        # list_by_provider already filters to enabled models. With no models
        # there is nothing to probe and no real model id to issue the liveness
        # call against — probing a fake "ping" model would 404 on most backends
        # and persist a bogus liveness failure for a valid-but-empty provider.
        # Return an empty result set and leave status untouched. (codex P2.)
        if not models:
            return []
        cfg = await self._config_from_provider(provider)
        factory = self._provider_factory_from_config(cfg, provider_name=provider.slug)
        liveness = await provider_probe.run_liveness(
            provider_factory=factory, model_id=models[0].model_id
        )
        await self._persist_provider_liveness(provider, liveness)
        if liveness.status != "pass":
            return [ProbeResult(overall="fail", blocking_failed=True, steps=[liveness])]
        fingerprint = capability_fingerprint(
            provider.capability or {}, provider.model_capability_overrides or {}
        )
        results: list[ProbeResult] = []
        for model in models:
            capability = self._resolve_capability(cfg, model.model_id, provider_name=provider.slug)
            model_result = await provider_probe.run_model_probe(
                provider_factory=factory, model_id=model.model_id, capability=capability
            )
            await self._persist_model_test(model, model_result, fingerprint)
            results.append(
                ProbeResult(
                    overall=model_result.overall,
                    blocking_failed=model_result.blocking_failed,
                    steps=[liveness, *model_result.steps],
                )
            )
        return results

    async def run_test_stream(
        self, provider_id: str, model_db_ids: list[str]
    ) -> AsyncIterator[bytes]:
        """Stream liveness (once) then a per-model probe event, persisting each verdict."""
        provider = await self.get_provider(provider_id)
        cfg = await self._config_from_provider(provider)
        factory = self._provider_factory_from_config(cfg, provider_name=provider.slug)
        models = {m.id: m for m in await self._models.list_all_for_provider(provider_id)}
        liveness = await provider_probe.run_liveness(
            provider_factory=factory, model_id=models[model_db_ids[0]].model_id
        )
        await self._persist_provider_liveness(provider, liveness)
        yield _sse("liveness", liveness.model_dump(mode="json"))
        if liveness.status != "pass":
            yield _sse("done", {"liveness": "fail"})
            return
        fingerprint = capability_fingerprint(
            provider.capability or {}, provider.model_capability_overrides or {}
        )
        for db_id in model_db_ids:
            model = models[db_id]
            cap = self._resolve_capability(cfg, model.model_id, provider_name=provider.slug)
            result = await provider_probe.run_model_probe(
                provider_factory=factory, model_id=model.model_id, capability=cap
            )
            await self._persist_model_test(model, result, fingerprint)
            yield _sse("model", {"model_db_id": db_id, **result.model_dump(mode="json")})
        yield _sse("done", {})

    async def preflight_test_stream(self, provider_id: str, model_db_ids: list[str]) -> None:
        """Validate provider + model ids before opening the SSE stream (errors → HTTP)."""
        await self.get_provider(provider_id)
        known = {m.id for m in await self._models.list_all_for_provider(provider_id)}
        missing = [i for i in model_db_ids if i not in known]
        if missing:
            raise ModelNotFoundError(f"models not found: {missing}")

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
        slug, model_id = parts
        provider = await self._providers.get_by_slug(slug)  # was get_by_name(provider_name)
        if provider is None:
            raise ValueError(f"Provider slug '{slug}' not found")
        if provider.org_id is None:
            override = await self._overrides.get(provider.id)
            if override and not override.enabled:
                raise ValueError(f"Provider '{slug}' is disabled by org")
        model = await self._models.get_by_model_id(provider.id, model_id)
        if model is None:
            raise ValueError(f"Model '{model_id}' not found in provider '{slug}'")
        if not model.enabled:
            raise ValueError(f"Model '{model_id}' is disabled")
