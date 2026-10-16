"""LLMSnapshot — per-request frozen view of LLM configuration.

A snapshot is loaded once per request via load_llm_snapshot(). Resolver
and builder modules take a snapshot as input and never read DB or
cubeplex.config themselves.

Immutability is enforced at the type level: fields are typed as Mapping
(read-only) so mypy strict rejects mutation. The underlying objects are
dicts; this contract is type-system enforcement, not runtime.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from cubeplex.credentials.encryption import EncryptionBackend
from cubeplex.llm.config import ProviderConfig
from cubeplex.llm.errors import CorruptPresetsRowError
from cubeplex.llm.readiness import (
    configuration_fingerprint,
    provider_configuration_fingerprint,
    verification_pending,
)
from cubeplex.llm.snapshot_schema import ModelPresetsConfig, ModelTier, TierSetting

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelPreset:
    key: str  # tier name or custom label
    primary: str
    fallbacks: tuple[str, ...]
    kind: Literal["tier", "custom"]
    is_default: bool
    description: str = ""  # "" for tiers (frontend supplies i18n copy by key)

    @property
    def chain(self) -> tuple[str, ...]:
        return (self.primary, *self.fallbacks)


@dataclass(frozen=True)
class LLMSnapshot:
    providers: Mapping[str, ProviderConfig]
    model_presets: tuple[ModelPreset, ...]
    task_routing: Mapping[str, str]


async def load_llm_snapshot(
    session: AsyncSession,
    org_id: str,
    encryption_backend: EncryptionBackend,
) -> LLMSnapshot:
    """Read DB providers + OrgSettings → frozen snapshot. No YAML."""
    providers = await _load_providers(session, org_id, encryption_backend)
    model_presets, task_routing = await _load_presets(session, org_id)
    return LLMSnapshot(providers=providers, model_presets=model_presets, task_routing=task_routing)


async def _load_providers(
    session: AsyncSession,
    org_id: str,
    backend: EncryptionBackend,
) -> dict[str, ProviderConfig]:
    from cubeplex.models import Credential
    from cubeplex.models.org_provider_override import OrgProviderOverride as DBO
    from cubeplex.models.provider import Model as DBM
    from cubeplex.models.provider import Provider as DBP

    stmt = (
        select(DBP, DBM, Credential)
        .outerjoin(DBO, and_(col(DBP.id) == col(DBO.provider_id), col(DBO.org_id) == org_id))
        .outerjoin(DBM, and_(col(DBM.provider_id) == col(DBP.id), col(DBM.enabled)))
        .outerjoin(Credential, col(Credential.id) == col(DBP.credential_id))
        .where(col(DBP.org_id).is_(None) | (col(DBP.org_id) == org_id))
        .where(func.coalesce(col(DBO.enabled), col(DBP.enabled), True))
        .order_by(col(DBP.org_id).nullsfirst(), col(DBP.id), col(DBM.model_id))
    )
    # One read snapshot keeps endpoint, credential version and model fields consistent.
    providers_by_id: dict[str, DBP] = {}
    models_by_provider: dict[str, list[DBM]] = {}
    cred_map: dict[str, Credential] = {}
    for provider, model, credential in (await session.execute(stmt)).all():
        providers_by_id[provider.id] = provider
        if model is not None:
            models_by_provider.setdefault(provider.id, []).append(model)
        if credential is not None:
            cred_map[credential.id] = credential
    rows = list(providers_by_id.values())

    out: dict[str, ProviderConfig] = {}
    for p in rows:
        models = models_by_provider.get(p.id, [])
        credential = cred_map.get(p.credential_id) if p.credential_id else None
        credential_revision = credential.value_revision if credential else 0
        api_key: str | None = None
        if p.credential_id is not None:
            cred = cred_map.get(p.credential_id)
            if cred is not None and cred.kind == "provider_api_key":
                try:
                    api_key = (await backend.decrypt(cred.value_encrypted)).decode("utf-8")
                except Exception:
                    logger.warning("decrypt failed for provider %s", p.name)
        out[p.slug] = ProviderConfig.model_validate(
            {
                "base_url": p.base_url,
                "provider_db_id": p.id,
                "org_id": p.org_id,
                "auth_type": p.auth_type,
                "credential_revision": credential_revision,
                "configuration_fingerprint": provider_configuration_fingerprint(
                    p, credential_revision
                ),
                "evidence_revision": p.evidence_revision,
                "probe_generation": p.probe_generation,
                "api_key": api_key,
                "api": p.provider_type,
                "extra_body": p.extra_body,
                "extra_headers": p.extra_headers,
                "capability": p.capability or {},
                "model_capability_overrides": p.model_capability_overrides or {},
                "models": [
                    {
                        "id": m.model_id,
                        "model_db_id": m.id,
                        "configuration_fingerprint": configuration_fingerprint(
                            p, m, credential_revision
                        ),
                        "evidence_revision": m.evidence_revision,
                        "probe_generation": m.probe_generation,
                        "configuration_metadata": m.configuration_metadata,
                        "reasoning_failed": not verification_pending(p, m, credential_revision)
                        and any(
                            step.get("name") == "reasoning" and step.get("status") == "fail"
                            for step in (m.last_test_summary or {}).get("steps", [])
                        ),
                        "image_verified": (
                            not m.configuration_metadata.get("vision_requires_verification")
                            or not verification_pending(p, m, credential_revision)
                        )
                        and any(
                            step.get("name") == "vision" and step.get("status") == "pass"
                            for step in (m.last_test_summary or {}).get("steps", [])
                        ),
                        "verification_pending": verification_pending(p, m, credential_revision),
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
                    for m in models
                ],
            }
        )
    return out


async def _load_presets(
    session: AsyncSession,
    org_id: str,
) -> tuple[tuple[ModelPreset, ...], dict[str, str]]:
    from cubeplex.models.org_settings import MODEL_PRESETS_KEY, OrgSettings

    # Org row overrides system row in full.
    org_stmt = select(OrgSettings).where(
        OrgSettings.org_id == org_id,  # type: ignore[arg-type]
        OrgSettings.key == MODEL_PRESETS_KEY,  # type: ignore[arg-type]
    )
    row = (await session.execute(org_stmt)).scalar_one_or_none()
    if row is None:
        sys_stmt = select(OrgSettings).where(
            OrgSettings.org_id.is_(None),  # type: ignore[union-attr]
            OrgSettings.key == MODEL_PRESETS_KEY,  # type: ignore[arg-type]
        )
        row = (await session.execute(sys_stmt)).scalar_one_or_none()
    if row is None:
        return (), {}
    try:
        cfg = ModelPresetsConfig.model_validate(row.value)
    except ValidationError as exc:
        raise CorruptPresetsRowError(org_id=row.org_id, errors=exc.errors()) from exc
    presets: list[ModelPreset] = []
    for tier in ModelTier:  # def order: lite, flash, pro, max
        s = cfg.tiers.get(tier, TierSetting())  # tiers config may omit disabled tiers
        if not s.enabled or not s.primary:
            continue
        presets.append(
            ModelPreset(
                key=tier.value,
                primary=s.primary,
                fallbacks=tuple(s.fallbacks),
                kind="tier",
                is_default=(cfg.default_preset == tier.value),
                description="",
            )
        )
    for c in cfg.custom_presets:
        presets.append(
            ModelPreset(
                key=c.label,
                primary=c.primary,
                fallbacks=tuple(c.fallbacks),
                kind="custom",
                is_default=(cfg.default_preset == c.label),
                description=c.description,
            )
        )
    return tuple(presets), {k.value: v for k, v in cfg.task_routing.items()}
