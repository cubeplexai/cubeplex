"""LLMSnapshot — per-request frozen view of LLM configuration.

A snapshot is loaded once per request via load_llm_snapshot(). Resolver
and builder modules take a snapshot as input and never read DB or
cubebox.config themselves.

Immutability is enforced at the type level: fields are typed as Mapping
(read-only) so mypy strict rejects mutation. The underlying objects are
dicts; this contract is type-system enforcement, not runtime.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.credentials.encryption import EncryptionBackend
from cubebox.llm.config import ProviderConfig
from cubebox.llm.errors import CorruptPresetsRowError
from cubebox.llm.snapshot_schema import ModelPresetsConfig, ModelTier

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
    return LLMSnapshot(
        providers=providers, model_presets=model_presets, task_routing=task_routing
    )


async def _load_providers(
    session: AsyncSession,
    org_id: str,
    backend: EncryptionBackend,
) -> dict[str, ProviderConfig]:
    from cubebox.models import Credential
    from cubebox.models.org_provider_override import OrgProviderOverride as DBO
    from cubebox.models.provider import Model as DBM
    from cubebox.models.provider import Provider as DBP

    stmt = (
        select(DBP)
        .outerjoin(
            DBO,
            (DBP.id == DBO.provider_id) & (DBO.org_id == org_id),  # type: ignore[arg-type]
        )
        .where(
            (DBP.org_id == None) | (DBP.org_id == org_id),  # type: ignore[arg-type]  # noqa: E711
        )
        .where(func.coalesce(DBO.enabled, DBP.enabled, True))
    )
    rows = (await session.execute(stmt)).scalars().all()

    # Batch-load enabled models for all providers in one query (was: one
    # SELECT per provider — N+1).
    provider_ids = [p.id for p in rows]
    models_by_provider: dict[str, list[DBM]] = {pid: [] for pid in provider_ids}
    if provider_ids:
        models_stmt = select(DBM).where(
            DBM.provider_id.in_(provider_ids),  # type: ignore[attr-defined]
            DBM.enabled,  # type: ignore[arg-type]
        )
        for m in (await session.execute(models_stmt)).scalars().all():
            models_by_provider.setdefault(m.provider_id, []).append(m)

    # Batch-load credentials in one query (was: session.get per provider).
    cred_ids = [p.credential_id for p in rows if p.credential_id is not None]
    cred_map: dict[str, Credential] = {}
    if cred_ids:
        cred_stmt = select(Credential).where(
            Credential.id.in_(cred_ids)  # type: ignore[attr-defined]
        )
        cred_map = {c.id: c for c in (await session.execute(cred_stmt)).scalars().all()}

    out: dict[str, ProviderConfig] = {}
    for p in rows:
        models = models_by_provider.get(p.id, [])
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
                    for m in models
                ],
            }
        )
    return out


async def _load_presets(
    session: AsyncSession,
    org_id: str,
) -> tuple[tuple[ModelPreset, ...], dict[str, str]]:
    from cubebox.models.org_settings import MODEL_PRESETS_KEY, OrgSettings

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
        s = cfg.tiers[tier]
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
