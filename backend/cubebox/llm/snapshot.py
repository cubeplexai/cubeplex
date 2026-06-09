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

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.credentials.encryption import EncryptionBackend
from cubebox.llm.config import ProviderConfig
from cubebox.llm.errors import CorruptPresetsRowError
from cubebox.llm.snapshot_schema import ModelPresetsValue

logger = logging.getLogger(__name__)


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


async def load_llm_snapshot(
    session: AsyncSession,
    org_id: str,
    encryption_backend: EncryptionBackend,
) -> LLMSnapshot:
    """Read DB providers + OrgSettings → frozen snapshot. No YAML."""
    providers = await _load_providers(session, org_id, encryption_backend)
    presets, task_presets = await _load_presets(session, org_id)
    return LLMSnapshot(providers=providers, presets=presets, task_presets=task_presets)


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
    out: dict[str, ProviderConfig] = {}
    for p in rows:
        models = (
            (
                await session.execute(
                    select(DBM).where(
                        DBM.provider_id == p.id,  # type: ignore[arg-type]
                        DBM.enabled,  # type: ignore[arg-type]
                    )
                )
            )
            .scalars()
            .all()
        )
        api_key: str | None = None
        if p.credential_id is not None:
            cred = await session.get(Credential, p.credential_id)
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
) -> tuple[tuple[LLMPreset, ...], dict[str, str]]:
    from cubebox.models.org_settings import MODEL_PRESETS_KEY, OrgSettings

    # Org row overrides system row in full.
    org_stmt = select(OrgSettings).where(
        OrgSettings.org_id == org_id,  # type: ignore[arg-type]
        OrgSettings.key == MODEL_PRESETS_KEY,  # type: ignore[arg-type]
    )
    org_row = (await session.execute(org_stmt)).scalar_one_or_none()
    if org_row is None:
        sys_stmt = select(OrgSettings).where(
            OrgSettings.org_id.is_(None),  # type: ignore[union-attr]
            OrgSettings.key == MODEL_PRESETS_KEY,  # type: ignore[arg-type]
        )
        org_row = (await session.execute(sys_stmt)).scalar_one_or_none()
    if org_row is None:
        return (), {}
    try:
        parsed = ModelPresetsValue.model_validate(org_row.value)
    except ValidationError as exc:
        raise CorruptPresetsRowError(
            org_id=org_row.org_id,
            errors=exc.errors(),
        ) from exc
    presets = tuple(
        LLMPreset(label=p.label, chain=tuple(p.chain), is_default=p.is_default)
        for p in parsed.presets
    )
    return presets, dict(parsed.task_presets)
