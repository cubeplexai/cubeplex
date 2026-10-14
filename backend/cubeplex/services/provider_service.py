"""ProviderService -- CRUD, invariants, two-phase test/liveness probe, seed."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from cubeplex.api.exceptions import ModelInUseByPresetError
from cubeplex.api.schemas.provider import (
    ModelCreate,
    ModelUpdate,
    ProviderCreate,
    ProviderLivenessRequest,
    ProviderTestRequest,
    ProviderUpdate,
)
from cubeplex.api.schemas.provider_discovery import (
    EnableTestedModelOutcome,
    EnableTestedModelsResponse,
    ImportModelOutcome,
    ImportModelsRequest,
    ImportModelsResponse,
)
from cubeplex.credentials.exceptions import CredentialNotFound
from cubeplex.llm.builder import build_provider
from cubeplex.llm.config import ModelConfig, ProviderConfig
from cubeplex.llm.discovery_types import CallingConfiguration
from cubeplex.llm.readiness import (
    configuration_fingerprint,
    model_activation_blocker,
    provider_configuration_fingerprint,
)
from cubeplex.llm.request_parameters import validate_request_extensions, validate_request_headers
from cubeplex.llm.snapshot import LLMSnapshot
from cubeplex.models.org_provider_override import OrgProviderOverride
from cubeplex.models.provider import Model, Provider
from cubeplex.models.provider_creation_receipt import ProviderCreationReceipt
from cubeplex.repositories.credential import CredentialRepository
from cubeplex.repositories.model import ModelRepository
from cubeplex.repositories.org_provider_override import OrgProviderOverrideRepository
from cubeplex.repositories.provider import ProviderRepository
from cubeplex.repositories.provider_creation_receipt import ProviderCreationReceiptRepository
from cubeplex.services import provider_probe
from cubeplex.services.credential import CredentialService
from cubeplex.services.model_presets import find_preset_refs_to_model
from cubeplex.services.provider_discovery import configured_value, review_selection
from cubeplex.services.provider_discovery_preview import (
    DiscoveryOperationError,
    DiscoveryPreviewStore,
    resolve_discovery_configuration,
)
from cubeplex.services.provider_probe import ProbeResult, ProbeStep
from cubeplex.services.provider_verification import ProbeInput
from cubeplex.utils.slug import slugify

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
        credential_service: CredentialService,
        session: AsyncSession,
        org_id: str,
        actor_user_id: str,
        preview_store: DiscoveryPreviewStore | None = None,
    ) -> None:
        self._providers = provider_repo
        self._models = model_repo
        self._overrides = override_repo
        self._credentials = credential_service
        self._session = session
        self.org_id = org_id
        self.actor_user_id = actor_user_id
        self._preview_store = preview_store
        self._receipts = ProviderCreationReceiptRepository(session, org_id=org_id)
        self.creation_recovered = False

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

    async def calling_configuration(self, provider: Provider) -> CallingConfiguration:
        return CallingConfiguration.model_validate(
            {
                "api": provider.provider_type,
                "base_url": provider.base_url,
                "api_key": await self._resolve_api_key(provider),
                "auth_type": provider.auth_type,
                "preset_slug": provider.preset_slug,
                "capability": provider.capability or {},
                "extra_body": provider.extra_body or {},
                "extra_headers": provider.extra_headers or {},
                "discovery": provider.discovery or None,
            }
        )

    async def _lock_provider(self, provider_id: str) -> Provider:
        self._check_not_system(await self.get_provider(provider_id))
        provider = await self._providers.lock(provider_id)
        if provider is None:
            raise ProviderNotFoundError("Provider not found")
        return provider

    async def _credential_revision(self, provider: Provider, *, lock: bool = False) -> int:
        if provider.credential_id is None:
            return 0
        repo = CredentialRepository(self._session, org_id=provider.org_id)
        credential = (
            await repo.lock(provider.credential_id)
            if lock
            else await repo.get(provider.credential_id)
        )
        return credential.value_revision if credential else 0

    def _check_revision(self, actual: int, expected: int | None) -> None:
        if expected != actual:
            raise DiscoveryOperationError(
                "configuration_changed", "Configuration changed; reload before saving."
            )

    async def create_provider(self, data: ProviderCreate) -> Provider:
        await self._receipts.cleanup_expired()
        if data.discovery_id:
            await self._receipts.lock_token(self.actor_user_id, data.discovery_id)
            receipt = await self._receipts.get(self.actor_user_id, data.discovery_id)
            if receipt:
                if receipt.expires_at <= datetime.now(UTC):
                    raise DiscoveryOperationError(
                        "discovery_expired", "Creation recovery expired; rediscover."
                    )
                if receipt.outcome == "deleted" or receipt.provider_id is None:
                    raise DiscoveryOperationError(
                        "provider_creation_deleted", "The created provider was deleted."
                    )
                provider = await self._lock_provider(receipt.provider_id)
                self.creation_recovered = True
                await self._session.commit()
                return provider
        self._check_oauth(data.auth_type)
        self._validate_auth_creds(data.auth_type, bool(data.api_key))
        config, _, _ = resolve_discovery_configuration(
            CallingConfiguration.model_validate(
                {
                    "api": data.provider_type,
                    "base_url": data.base_url,
                    "api_key": data.api_key,
                    "auth_type": data.auth_type,
                    "preset_slug": data.preset_slug,
                    **(
                        {"capability": data.capability}
                        if "capability" in data.model_fields_set
                        else {}
                    ),
                    "extra_body": data.extra_body,
                    "extra_headers": data.extra_headers,
                    "discovery": data.discovery,
                }
            )
        )
        validate_request_headers(config.api, config.extra_headers)
        validate_request_extensions(config.api, config.extra_body, config.capability)
        if config.extra_headers and config.api != "openai-completions":
            raise ValueError("Extra headers are unsupported by this calling adapter")
        for capability in data.model_capability_overrides.values():
            validate_request_extensions(config.api, {}, capability)
        if data.discovery_id:
            if self._preview_store is None:
                raise RuntimeError("Discovery preview store is unavailable")
            preview = await self._preview_store.get(
                data.discovery_id, org_id=self.org_id, actor_user_id=self.actor_user_id
            )
            if preview.provider_id is not None:
                raise DiscoveryOperationError(
                    "discovery_stale", "Use the saved provider for this preview."
                )
            self._preview_store.verify(preview, config, None)
        if await self._providers.get_by_name(data.name) is not None:
            raise ProviderNameConflictError("Provider name already exists")
        slug = await self._resolve_slug(data.name, data.slug)
        try:
            credential_id = None
            if data.auth_type != "none" and data.api_key:
                credential_id = await self._credentials.stage_create(
                    kind=_PROVIDER_KEY_KIND,
                    name=data.name,
                    plaintext=data.api_key,
                )
            provider = Provider(
                org_id=self.org_id,
                name=data.name,
                slug=slug,
                provider_type=config.api,
                base_url=config.base_url,
                auth_type=data.auth_type,
                credential_id=credential_id,
                logo_url=data.logo_url,
                extra_body=data.extra_body,
                extra_headers=data.extra_headers,
                preset_slug=data.preset_slug,
                capability=config.capability,
                model_capability_overrides=data.model_capability_overrides,
                discovery=data.discovery.model_dump(mode="json") if data.discovery else {},
                created_by_user_id=self.actor_user_id,
            )
            self._session.add(provider)
            await self._session.flush()
            if data.discovery_id:
                self._session.add(
                    ProviderCreationReceipt(
                        org_id=self.org_id,
                        created_by_user_id=self.actor_user_id,
                        creation_discovery_id=data.discovery_id,
                        provider_id=provider.id,
                        expires_at=datetime.now(UTC) + timedelta(hours=24),
                    )
                )
            await self._session.commit()
            await self._session.refresh(provider)
            return provider
        except BaseException:
            await self._session.rollback()
            raise

    async def update_provider(self, provider_id: str, data: ProviderUpdate) -> Provider:
        p = await self._lock_provider(provider_id)
        self._check_revision(p.configuration_revision, data.expected_configuration_revision)
        await self._credential_revision(p, lock=True)
        effective_auth = data.auth_type if data.auth_type is not None else p.auth_type
        will_have_key = (
            bool(data.api_key)
            if data.api_key is not None
            else (p.credential_id is not None and effective_auth != "none")
        )
        self._check_oauth(effective_auth)
        self._validate_auth_creds(effective_auth, will_have_key)
        if data.name is not None and data.name != p.name:
            if await self._providers.get_by_name(data.name) is not None:
                raise ProviderNameConflictError("Provider name already exists")
            if p.credential_id:
                await self._credentials.stage_update(credential_id=p.credential_id, name=data.name)
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
        ):
            value = getattr(data, field, None)
            if value is not None:
                setattr(p, field, value)
        if data.discovery is not None:
            p.discovery = data.discovery.model_dump(mode="json")
        overrides = dict(p.model_capability_overrides or {})
        for change in data.model_capability_updates or []:
            model = await self._models.get(change.model_db_id)
            if model is None or model.provider_id != p.id:
                raise ModelNotFoundError("Model not found")
            self._check_revision(
                model.configuration_revision, change.expected_configuration_revision
            )
            if change.capability is None:
                overrides.pop(model.model_id, None)
            else:
                validate_request_extensions(p.provider_type, {}, change.capability)
                overrides[model.model_id] = change.capability
            model.configuration_revision += 1
            self._session.add(model)
        p.model_capability_overrides = overrides
        validate_request_headers(p.provider_type, p.extra_headers or {})
        validate_request_extensions(p.provider_type, p.extra_body or {}, p.capability or {})
        if p.extra_headers and p.provider_type != "openai-completions":
            raise ValueError("Extra headers are unsupported by this calling adapter")
        if effective_auth == "none" or data.api_key == "":
            old = p.credential_id
            p.credential_id = None
            await self._session.flush()
            if old:
                await self._credentials.stage_delete(credential_id=old)
        elif data.api_key is not None:
            if p.credential_id:
                await self._credentials.stage_update(
                    credential_id=p.credential_id, plaintext=data.api_key
                )
            else:
                p.credential_id = await self._credentials.stage_create(
                    kind=_PROVIDER_KEY_KIND,
                    name=p.name,
                    plaintext=data.api_key,
                )
        if data.enabled is not None:
            p.enabled = data.enabled
        config, _, _ = resolve_discovery_configuration(await self.calling_configuration(p))
        p.base_url = config.base_url
        p.capability = config.capability
        p.configuration_revision += 1
        p.updated_at = datetime.now(UTC)
        self._session.add(p)
        await self._session.commit()
        await self._session.refresh(p)
        return p

    async def delete_provider(self, provider_id: str) -> None:
        self._check_not_system(await self.get_provider(provider_id))
        receipts = await self._receipts.for_provider(provider_id)
        for receipt in receipts:
            await self._receipts.lock_token(
                receipt.created_by_user_id, receipt.creation_discovery_id
            )
            current = await self._receipts.get(
                receipt.created_by_user_id, receipt.creation_discovery_id
            )
            if current:
                current.outcome = "deleted"
                self._session.add(current)
        p = await self._lock_provider(provider_id)
        await self._credential_revision(p, lock=True)
        cred_id = p.credential_id
        await self._models.delete_by_provider(provider_id)
        await self._overrides.delete(provider_id)
        await self._providers.delete(p)
        await self._session.flush()
        if cred_id:
            await self._credentials.stage_delete(credential_id=cred_id)
        await self._session.commit()

    # -- Model CRUD -------------------------------------------------------------

    async def import_models(
        self, provider_id: str, data: ImportModelsRequest
    ) -> ImportModelsResponse:
        if self._preview_store is None:
            raise RuntimeError("Discovery preview store is unavailable")
        preview = await self._preview_store.get(
            data.discovery_id, org_id=self.org_id, actor_user_id=self.actor_user_id
        )
        provider = await self._lock_provider(provider_id)
        await self._credential_revision(provider, lock=True)
        config = await self.calling_configuration(provider)
        self._preview_store.verify(preview, config, provider_id)
        candidates = {candidate.model_id: candidate for candidate in preview.response.candidates}
        existing = {
            model.model_id: model for model in await self._models.list_all_for_provider(provider_id)
        }
        if not {item.model_id for item in data.models} <= candidates.keys():
            raise DiscoveryOperationError("unknown_model", "Select IDs from this preview.", 422)
        staged: list[tuple[Model, dict[str, Any]]] = []
        outcomes: list[ImportModelOutcome] = []
        from cubeloop.providers.capability import CapabilityDescriptor

        for selection in data.models:
            old = existing.get(selection.model_id)
            if old:
                outcomes.append(
                    ImportModelOutcome(model_id=old.model_id, model_db_id=old.id, status="existing")
                )
                continue
            reviewed = review_selection(candidates[selection.model_id], selection)
            raw_capability = configured_value(reviewed, "capability")
            capability = CapabilityDescriptor.model_validate(
                raw_capability if raw_capability is not None else provider.capability or {}
            )
            if not raw_capability or not configured_value(reviewed, "reasoning", False):
                capability.reasoning = None
            modalities = [
                value
                for value in configured_value(reviewed, "input_modalities", [])
                if value in {"text", "image"}
            ]
            capability.supports_images = "image" in modalities
            capability.supports_tools = True
            capability.supports_streaming = True
            descriptor = capability.model_dump(mode="json")
            validate_request_extensions(provider.provider_type, {}, descriptor)
            fields = {
                field: evidence.model_dump(mode="json")
                for field, evidence in reviewed.fields.items()
            }
            model = Model(
                org_id=self.org_id,
                provider_id=provider_id,
                model_id=reviewed.model_id,
                display_name=reviewed.display_name,
                enabled=False,
                context_window=configured_value(reviewed, "context_window"),
                max_tokens=configured_value(reviewed, "max_tokens"),
                input_modalities=modalities,
                reasoning=bool(configured_value(reviewed, "reasoning", False)),
                cost_input=configured_value(reviewed, "cost_input", 0),
                cost_output=configured_value(reviewed, "cost_output", 0),
                cost_cache_read=configured_value(reviewed, "cost_cache_read", 0),
                cost_cache_write=configured_value(reviewed, "cost_cache_write", 0),
                configuration_metadata={
                    "origin": "discovery",
                    "discovery_id": data.discovery_id,
                    "fields": fields,
                    "effective_capability": descriptor,
                },
            )
            staged.append((model, descriptor))
            outcomes.append(
                ImportModelOutcome(model_id=model.model_id, model_db_id=model.id, status="created")
            )
        overrides = dict(provider.model_capability_overrides or {})
        for model, descriptor in staged:
            self._session.add(model)
            overrides[model.model_id] = descriptor
        if staged:
            provider.model_capability_overrides = overrides
            provider.configuration_revision += 1
            self._session.add(provider)
        await self._session.commit()
        return ImportModelsResponse(results=outcomes)

    async def enable_tested_models(
        self, provider_id: str, model_db_ids: list[str]
    ) -> EnableTestedModelsResponse:
        provider = await self._lock_provider(provider_id)
        credential_revision = await self._credential_revision(provider, lock=True)
        models: list[Model] = []
        for model_db_id in dict.fromkeys(model_db_ids):
            model = await self._models.get(model_db_id)
            if model is None or model.provider_id != provider_id:
                raise ModelNotFoundError("Model not found")
            models.append(model)
        results: list[EnableTestedModelOutcome] = []
        for model in models:
            reason = (
                None
                if model.enabled
                else model_activation_blocker(provider, model, credential_revision)
            )
            if reason:
                results.append(
                    EnableTestedModelOutcome(model_db_id=model.id, status="blocked", reason=reason)
                )
            else:
                model.enabled = True
                model.configuration_revision += 1
                self._session.add(model)
                results.append(EnableTestedModelOutcome(model_db_id=model.id, status="enabled"))
        await self._session.commit()
        return EnableTestedModelsResponse(results=results)

    async def list_models(self, provider_id: str) -> list[Model]:
        await self.get_provider(provider_id)
        return await self._models.list_by_provider(provider_id)

    async def create_model(self, provider_id: str, data: ModelCreate) -> Model:
        provider = await self._lock_provider(provider_id)
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
        validate_request_headers(provider.provider_type, m.extra_headers or {})
        validate_request_extensions(
            provider.provider_type,
            m.extra_body or {},
            provider.model_capability_overrides.get(m.model_id, provider.capability or {}),
        )
        if m.context_window <= 0 or m.max_tokens <= 0 or m.max_tokens > m.context_window:
            raise ValueError("Model limits must be positive and output must fit within context")
        fields = {
            field: {"value": getattr(m, field), "source": "administrator", "state": "confirmed"}
            for field in (
                "context_window",
                "max_tokens",
                "input_modalities",
                "reasoning",
                "cost_input",
                "cost_output",
                "cost_cache_read",
                "cost_cache_write",
            )
            if field in data.model_fields_set
        }
        m.configuration_metadata = {
            "origin": "manual",
            "fields": fields,
            "vision_requires_verification": "image" in m.input_modalities,
        }
        return await self._models.add(m)

    async def update_model(self, provider_id: str, model_db_id: str, data: ModelUpdate) -> Model:
        provider = await self._lock_provider(provider_id)
        m = await self._models.get(model_db_id)
        if m is None or m.provider_id != provider_id:
            raise ModelNotFoundError(f"Model {model_db_id} not found")
        self._check_revision(m.configuration_revision, data.expected_configuration_revision)
        credential_revision = await self._credential_revision(provider, lock=True)
        adding_image = "image" not in m.input_modalities and "image" in (
            data.input_modalities or []
        )
        activating_import = (
            data.enabled is True
            and not m.enabled
            and m.configuration_metadata.get("origin") == "discovery"
        )
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
        validate_request_headers(provider.provider_type, m.extra_headers or {})
        validate_request_extensions(
            provider.provider_type,
            m.extra_body or {},
            provider.model_capability_overrides.get(m.model_id, provider.capability or {}),
        )
        if m.context_window <= 0 or m.max_tokens <= 0 or m.max_tokens > m.context_window:
            raise ValueError("Model limits must be positive and output must fit within context")
        if activating_import:
            reason = model_activation_blocker(provider, m, credential_revision)
            if reason:
                raise DiscoveryOperationError(reason, "Verify this configuration before enabling.")
        metadata = dict(m.configuration_metadata or {})
        fields = dict(metadata.get("fields", {}))
        for field in data.model_fields_set & {
            "context_window",
            "max_tokens",
            "input_modalities",
            "reasoning",
            "cost_input",
            "cost_output",
            "cost_cache_read",
            "cost_cache_write",
        }:
            if getattr(data, field) is not None:
                fields[field] = {
                    "value": getattr(m, field),
                    "source": "administrator",
                    "state": "confirmed",
                }
        metadata["fields"] = fields
        if adding_image:
            metadata["vision_requires_verification"] = True
        m.configuration_metadata = metadata
        m.configuration_revision += 1
        m.updated_at = datetime.now(UTC)
        return await self._models.update(m)

    async def delete_model(self, provider_id: str, model_db_id: str) -> None:
        provider = await self._lock_provider(provider_id)
        m = await self._models.get(model_db_id)
        if m is None or m.provider_id != provider_id:
            raise ModelNotFoundError(f"Model {model_db_id} not found")
        # Per D6: scan caller-org row first. If absent, fall back to the
        # system row — its presets are the org's effective presets, so a
        # delete that ignores them would break the next run. Other orgs'
        # rows are never scanned (cross-tenant info leak).
        refs = await find_preset_refs_to_model(
            self._session, self.org_id, provider.slug, m.model_id
        )
        if refs:
            raise ModelInUseByPresetError(
                slug=provider.slug,
                model_id=m.model_id,
                refs=[
                    {
                        # System-row refs are conceptually owned by no org;
                        # surface that truthfully so consumers branching on
                        # org_id don't get misled. The `source` field is the
                        # canonical disambiguator either way.
                        "org_id": None if r["source"] == "system" else self.org_id,
                        "preset_label": r["preset_label"],
                        "source": r["source"],
                    }
                    for r in refs
                ],
            )
        await self._models.delete(m)

    # -- Two-phase test / liveness probe ----------------------------------------

    def _provider_factory_from_config(
        self, cfg: ProviderConfig, *, provider_name: str, model_id: str | None = None
    ) -> Callable[[], Any]:
        """Zero-arg callable that builds a fresh cubeloop provider for the probe.

        The probe orchestrators call the factory each phase, so each invocation
        must yield an independent provider instance.
        """

        def factory() -> Any:
            snap = LLMSnapshot(providers={provider_name: cfg}, model_presets=(), task_routing={})
            provider = build_provider(snap, provider_name, cache_policy=None)
            if model_id:
                configured = next((model for model in cfg.models if model.id == model_id), None)
                if configured:
                    provider._cubeplex_probe_model = configured
                    if hasattr(provider, "_extra_body"):
                        provider._extra_body = {
                            **(cfg.extra_body or {}),
                            **(configured.extra_body or {}),
                        }
                    if configured.extra_headers and cfg.api == "openai-completions":
                        provider._client = provider._client.with_options(
                            default_headers={
                                **(cfg.extra_headers or {}),
                                **configured.extra_headers,
                            }
                        )
            return provider

        return factory

    def _resolve_capability(self, cfg: ProviderConfig, model_id: str, *, provider_name: str) -> Any:
        """Effective CapabilityDescriptor for ``model_id`` (override > base).

        Built off a throwaway cubeloop provider so we reuse the exact same merge
        logic the runtime uses (``provider._resolve_capability``), avoiding a
        second copy of the override-precedence rule.
        """
        snap = LLMSnapshot(providers={provider_name: cfg}, model_presets=(), task_routing={})
        provider = build_provider(snap, provider_name, cache_policy=None)
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
        models = await self._models.list_all_for_provider(provider.id)
        return ProviderConfig(
            api=provider.provider_type,
            auth_type=provider.auth_type,
            api_key=api_key or "placeholder",
            base_url=provider.base_url,
            extra_body=provider.extra_body or {},
            extra_headers=provider.extra_headers or {},
            capability=provider.capability or {},
            model_capability_overrides=provider.model_capability_overrides or {},
            models=[
                ModelConfig(
                    id=model.model_id,
                    name=model.display_name,
                    reasoning=model.reasoning,
                    input=model.input_modalities,
                    contextWindow=model.context_window,
                    maxTokens=model.max_tokens,
                    extra_body=model.extra_body or {},
                    extra_headers=model.extra_headers or {},
                )
                for model in models
            ],
        )

    async def _claim_probes(
        self, provider_id: str, model_db_ids: list[str], *, claim_models: bool = True
    ) -> list[ProbeInput]:
        await self.get_provider(provider_id)
        provider_stmt = (
            select(Provider)
            .where(
                Provider.id == provider_id,  # type: ignore[arg-type]
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        provider = (await self._session.execute(provider_stmt)).scalar_one_or_none()
        if provider is None:
            raise ProviderNotFoundError("Provider not found")
        credential_revision = await self._credential_revision(provider, lock=True)
        stmt = (
            select(Model)
            .where(
                Model.provider_id == provider_id,  # type: ignore[arg-type]
                Model.id.in_(model_db_ids),  # type: ignore[attr-defined]
            )
            .order_by(Model.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        models = list((await self._session.execute(stmt)).scalars().all())
        if {model.id for model in models} != set(model_db_ids):
            raise ModelNotFoundError("Model not found")
        config = await self._config_from_provider(provider)
        provider.probe_generation += 1
        self._session.add(provider)
        claims: list[ProbeInput] = []
        for model in models:
            if claim_models:
                model.probe_generation += 1
                self._session.add(model)
            claims.append(
                ProbeInput(
                    provider_id=provider.id,
                    model_db_id=model.id,
                    model_id=model.model_id,
                    configuration_fingerprint=configuration_fingerprint(
                        provider, model, credential_revision
                    ),
                    provider_fingerprint=provider_configuration_fingerprint(
                        provider, credential_revision
                    ),
                    probe_generation=model.probe_generation,
                    provider_probe_generation=provider.probe_generation,
                    provider_evidence_revision=provider.evidence_revision,
                    model_evidence_revision=model.evidence_revision,
                    config=config.model_copy(deep=True),
                )
            )
        await self._session.commit()
        by_id = {claim.model_db_id: claim for claim in claims}
        return [by_id[db_id] for db_id in dict.fromkeys(model_db_ids)]

    async def _current_probe_rows(self, claim: ProbeInput) -> tuple[Provider, Model, int] | None:
        stmt = (
            select(Provider)
            .where(col(Provider.id) == claim.provider_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        provider = (await self._session.execute(stmt)).scalar_one_or_none()
        if provider is None or provider.org_id not in {None, self.org_id}:
            return None
        revision = await self._credential_revision(provider, lock=True)
        stmt_model = (
            select(Model)
            .where(col(Model.id) == claim.model_db_id, col(Model.provider_id) == provider.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        model = (await self._session.execute(stmt_model)).scalar_one_or_none()
        if (
            model is None
            or configuration_fingerprint(provider, model, revision)
            != claim.configuration_fingerprint
        ):
            return None
        if (
            model.probe_generation != claim.probe_generation
            or provider.probe_generation != claim.provider_probe_generation
        ):
            return None
        return provider, model, revision

    async def _accept_liveness(self, claim: ProbeInput, step: ProbeStep) -> tuple[bool, int]:
        rows = await self._current_probe_rows(claim)
        if rows is None or rows[0].evidence_revision != claim.provider_evidence_revision:
            await self._session.rollback()
            return False, claim.provider_evidence_revision
        provider, model, _ = rows
        if model.evidence_revision != claim.model_evidence_revision:
            await self._session.rollback()
            return False, claim.provider_evidence_revision
        provider.last_liveness_status = provider_probe.liveness_status_for(step)
        provider.last_liveness_at = datetime.now(UTC)
        provider.evidence_revision += 1
        provider.last_liveness_summary = {
            **step.model_dump(mode="json"),
            "configuration_fingerprint": claim.provider_fingerprint,
            "probe_generation": provider.probe_generation,
            "evidence_revision": provider.evidence_revision,
        }
        self._session.add(provider)
        revision = provider.evidence_revision
        await self._session.commit()
        return True, revision

    async def _accept_model_result(self, claim: ProbeInput, result: ProbeResult) -> bool:
        rows = await self._current_probe_rows(claim)
        if (
            rows is None
            or rows[0].evidence_revision != claim.provider_evidence_revision
            or rows[1].evidence_revision != claim.model_evidence_revision
        ):
            await self._session.rollback()
            return False
        provider, model, _ = rows
        auth_error = next(
            (
                step.error
                for step in result.steps
                if step.error is not None and provider_probe._error_says_auth_failure(step.error)
            ),
            None,
        )
        if auth_error is not None:
            provider.evidence_revision += 1
            provider.last_liveness_status = "auth_error"
            provider.last_liveness_at = datetime.now(UTC)
            provider.last_liveness_summary = {
                "source": "model_probe",
                "detail": "Provider credential rejected",
                "configuration_fingerprint": claim.provider_fingerprint,
                "probe_generation": claim.provider_probe_generation,
                "evidence_revision": provider.evidence_revision,
            }
            self._session.add(provider)
        model.evidence_revision += 1
        required = {step.name: step.status for step in result.steps}
        optional = all(
            step.status != "fail" for step in result.steps if step.name in {"reasoning", "vision"}
        )
        model.last_test_status = _OVERALL_TO_STATUS[result.overall]
        model.last_test_at = datetime.now(UTC)
        if required.get("vision") == "pass":
            model.configuration_metadata = {
                **model.configuration_metadata,
                "vision_requires_verification": False,
            }
        model.last_test_summary = {
            **result.model_dump(mode="json"),
            "configuration_fingerprint": claim.configuration_fingerprint,
            "probe_generation": claim.probe_generation,
            "evidence_revision": model.evidence_revision,
            "required_checks_passed": required.get("tools") == "pass"
            and required.get("streaming") == "pass"
            and optional,
        }
        self._session.add(model)
        await self._session.commit()
        return True

    def _redact_probe(self, result: ProbeResult, cfg: ProviderConfig) -> ProbeResult:
        for step in result.steps:
            if cfg.api_key:
                step.detail = step.detail.replace(cfg.api_key, "[redacted]")
                if step.error:
                    step.error.message = step.error.message.replace(cfg.api_key, "[redacted]")
        return result

    async def run_liveness_dryrun(self, req: ProviderLivenessRequest) -> ProbeStep:
        """Pre-save liveness — transient provider, no DB write (spec §4.3)."""
        cfg = self._config_from_request(req)
        return await provider_probe.run_liveness(
            provider_factory=self._provider_factory_from_config(cfg, provider_name=req.api),
            model_id=req.model_id,
        )

    async def run_liveness_saved(self, provider_id: str, model_id: str) -> ProbeStep:
        provider = await self.get_provider(provider_id)
        model = await self._models.get_by_model_id(provider_id, model_id)
        if model is None:
            raise ModelNotFoundError("Select a saved model for liveness testing")
        claims = await self._claim_probes(provider_id, [model.id], claim_models=False)
        claim = claims[0]
        factory = self._provider_factory_from_config(
            claim.config, provider_name=provider.slug, model_id=model_id
        )
        step = await provider_probe.run_liveness(provider_factory=factory, model_id=model_id)
        self._redact_probe(
            ProbeResult(overall="pass", blocking_failed=False, steps=[step]), claim.config
        )
        await self._accept_liveness(claim, step)
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
        results: list[ProbeResult] = []
        liveness: ProbeStep | None = None
        async for encoded in self.run_test_stream(provider_id, [model_db_id]):
            event, payload = encoded.decode().split("\ndata: ", 1)
            value = json.loads(payload)
            if event == "event: model":
                results.append(ProbeResult.model_validate(value))
            elif event == "event: liveness":
                liveness = ProbeStep.model_validate(value)
            if event == "event: liveness" and value.get("status") != "pass":
                return ProbeResult(
                    overall="fail", blocking_failed=True, steps=[ProbeStep.model_validate(value)]
                )
        return (
            results[0].model_copy(
                update={"steps": [*([liveness] if liveness else []), *results[0].steps]}
            )
            if results
            else ProbeResult(overall="fail", blocking_failed=True, current=False)
        )

    async def run_all_models_test_saved(self, provider_id: str) -> list[ProbeResult]:
        await self.get_provider(provider_id)
        models = await self._models.list_by_provider(provider_id)
        if not models:
            return []
        results: list[ProbeResult] = []
        async for encoded in self.run_test_stream(provider_id, [model.id for model in models]):
            event, payload = encoded.decode().split("\ndata: ", 1)
            value = json.loads(payload)
            if event == "event: model":
                results.append(ProbeResult.model_validate(value))
        return results

    async def run_test_stream(
        self, provider_id: str, model_db_ids: list[str]
    ) -> AsyncIterator[bytes]:
        claims = await self._claim_probes(provider_id, model_db_ids)
        first = claims[0]
        factory = self._provider_factory_from_config(
            first.config, provider_name=provider_id, model_id=first.model_id
        )
        liveness = await provider_probe.run_liveness(
            provider_factory=factory, model_id=first.model_id
        )
        self._redact_probe(
            ProbeResult(overall="pass", blocking_failed=False, steps=[liveness]), first.config
        )
        accepted, revision = await self._accept_liveness(first, liveness)
        yield _sse("liveness", {**liveness.model_dump(mode="json"), "current": accepted})
        if not accepted or liveness.status != "pass":
            yield _sse("done", {"current": accepted, "liveness": liveness.status})
            return
        claims = [replace(claim, provider_evidence_revision=revision) for claim in claims]
        semaphore = asyncio.Semaphore(2)

        async def worker(claim: ProbeInput) -> tuple[ProbeInput, ProbeResult]:
            async with semaphore:
                factory = self._provider_factory_from_config(
                    claim.config, provider_name=provider_id, model_id=claim.model_id
                )
                capability = self._resolve_capability(
                    claim.config, claim.model_id, provider_name=provider_id
                )
                configured = next(
                    model for model in claim.config.models if model.id == claim.model_id
                )
                if not configured.reasoning:
                    capability = capability.model_copy(update={"reasoning": None})
                result = await provider_probe.run_model_probe(
                    provider_factory=factory, model_id=claim.model_id, capability=capability
                )
                return claim, self._redact_probe(result, claim.config)

        tasks = [asyncio.create_task(worker(claim)) for claim in claims]
        try:
            for task in asyncio.as_completed(tasks):
                claim, result = await task
                accepted = await self._accept_model_result(claim, result)
                result.current = accepted
                yield _sse(
                    "model", {"model_db_id": claim.model_db_id, **result.model_dump(mode="json")}
                )
                if accepted and any(
                    step.error is not None and provider_probe._error_says_auth_failure(step.error)
                    for step in result.steps
                ):
                    yield _sse(
                        "liveness",
                        {
                            "name": "liveness",
                            "status": "fail",
                            "detail": "Provider credential rejected during model verification.",
                            "current": True,
                        },
                    )
                    break
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
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
