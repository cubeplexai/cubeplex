"""Service-layer for OrgSettings.model_presets read/write + delete guards."""

from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.api.schemas.model_presets import AdminModelPresetsBody
from cubeplex.llm.errors import BrokenPresetError
from cubeplex.models.org_settings import MODEL_PRESETS_KEY, OrgSettings


async def read_org_presets(
    session: AsyncSession,
    org_id: str,
) -> tuple[AdminModelPresetsBody | None, Literal["org", "system", "none"]]:
    """Return org row if present, else system row, else (None, 'none')."""
    org_stmt = select(OrgSettings).where(
        OrgSettings.org_id == org_id,  # type: ignore[arg-type]
        OrgSettings.key == MODEL_PRESETS_KEY,  # type: ignore[arg-type]
    )
    row = (await session.execute(org_stmt)).scalar_one_or_none()
    if row is not None:
        return AdminModelPresetsBody.model_validate(row.value), "org"

    sys_stmt = select(OrgSettings).where(
        OrgSettings.org_id.is_(None),  # type: ignore[union-attr]
        OrgSettings.key == MODEL_PRESETS_KEY,  # type: ignore[arg-type]
    )
    row = (await session.execute(sys_stmt)).scalar_one_or_none()
    if row is not None:
        return AdminModelPresetsBody.model_validate(row.value), "system"
    return None, "none"


def _available_chains(body: AdminModelPresetsBody) -> list[tuple[str, list[str]]]:
    """Return (preset_key, chain) for every available (enabled/custom) preset."""
    out: list[tuple[str, list[str]]] = []
    for tier, s in body.tiers.items():
        if s.enabled and s.primary:
            out.append((tier.value, [s.primary, *s.fallbacks]))
    for c in body.custom_presets:
        out.append((c.label, [c.primary, *c.fallbacks]))
    return out


async def write_org_presets(
    session: AsyncSession,
    org_id: str,
    body: AdminModelPresetsBody,
    *,
    available_models: set[str],
) -> None:
    """Upsert OrgSettings.model_presets for org. Raises BrokenPresetError on unknown refs."""
    missing: list[str] = []
    for _key, chain in _available_chains(body):
        for ref in chain:
            if ref not in available_models:
                missing.append(ref)
    if missing:
        raise BrokenPresetError(label="<admin write>", missing_refs=missing)

    existing_stmt = select(OrgSettings).where(
        OrgSettings.org_id == org_id,  # type: ignore[arg-type]
        OrgSettings.key == MODEL_PRESETS_KEY,  # type: ignore[arg-type]
    )
    existing = (await session.execute(existing_stmt)).scalar_one_or_none()
    payload = body.model_dump(mode="json")
    if existing is None:
        session.add(OrgSettings(org_id=org_id, key=MODEL_PRESETS_KEY, value=payload))
    else:
        existing.value = payload
    await session.flush()


def _refs_to_model_in_row(value: dict[str, object], ref: str, source: str) -> list[dict[str, str]]:
    """Scan a stored ModelPresetsConfig dict for presets whose chain contains ``ref``."""
    body = AdminModelPresetsBody.model_validate(value)
    return [
        {"preset_label": key, "source": source}
        for key, chain in _available_chains(body)
        if ref in chain
    ]


async def find_preset_refs_to_model(
    session: AsyncSession,
    org_id: str,
    slug: str,
    model_id: str,
) -> list[dict[str, str]]:
    """Return ``{preset_label, source}`` entries for presets that reference this ref.

    Scans the caller's own org row first. If the org has no row, falls back
    to the system row — the org's effective presets come from there, so
    deleting a model referenced by a system preset would break the org's
    next run (broken_preset at load time).

    Other orgs' rows are NEVER scanned (cross-tenant info leak per D6).
    """
    ref = f"{slug}/{model_id}"
    org_stmt = select(OrgSettings).where(
        OrgSettings.org_id == org_id,  # type: ignore[arg-type]
        OrgSettings.key == MODEL_PRESETS_KEY,  # type: ignore[arg-type]
    )
    org_row = (await session.execute(org_stmt)).scalar_one_or_none()
    if org_row is not None:
        return _refs_to_model_in_row(org_row.value, ref, "org")
    sys_stmt = select(OrgSettings).where(
        OrgSettings.org_id.is_(None),  # type: ignore[union-attr]
        OrgSettings.key == MODEL_PRESETS_KEY,  # type: ignore[arg-type]
    )
    sys_row = (await session.execute(sys_stmt)).scalar_one_or_none()
    if sys_row is None:
        return []
    return _refs_to_model_in_row(sys_row.value, ref, "system")
