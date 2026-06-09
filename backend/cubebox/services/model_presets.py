"""Service-layer for OrgSettings.model_presets read/write + delete guards."""

from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.api.schemas.model_presets import AdminModelPresetsBody
from cubebox.llm.errors import BrokenPresetError
from cubebox.models.org_settings import MODEL_PRESETS_KEY, OrgSettings


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


async def write_org_presets(
    session: AsyncSession,
    org_id: str,
    body: AdminModelPresetsBody,
    *,
    available_models: set[str],
) -> None:
    """Upsert OrgSettings.model_presets for org. Raises BrokenPresetError on unknown refs."""
    missing: list[str] = []
    for preset in body.presets:
        for ref in preset.chain:
            if ref not in available_models:
                missing.append(ref)
    if missing:
        raise BrokenPresetError(
            label="<admin write>",
            missing_refs=missing,
        )

    existing_stmt = select(OrgSettings).where(
        OrgSettings.org_id == org_id,  # type: ignore[arg-type]
        OrgSettings.key == MODEL_PRESETS_KEY,  # type: ignore[arg-type]
    )
    existing = (await session.execute(existing_stmt)).scalar_one_or_none()
    payload = body.model_dump()
    if existing is None:
        session.add(OrgSettings(org_id=org_id, key=MODEL_PRESETS_KEY, value=payload))
    else:
        existing.value = payload
    await session.flush()


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
        return [
            {"preset_label": preset["label"], "source": "org"}
            for preset in org_row.value.get("presets", [])
            if ref in preset.get("chain", [])
        ]
    sys_stmt = select(OrgSettings).where(
        OrgSettings.org_id.is_(None),  # type: ignore[union-attr]
        OrgSettings.key == MODEL_PRESETS_KEY,  # type: ignore[arg-type]
    )
    sys_row = (await session.execute(sys_stmt)).scalar_one_or_none()
    if sys_row is None:
        return []
    return [
        {"preset_label": preset["label"], "source": "system"}
        for preset in sys_row.value.get("presets", [])
        if ref in preset.get("chain", [])
    ]
