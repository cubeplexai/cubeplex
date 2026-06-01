"""SandboxPolicy repository — keyed on org_id only (no workspace dimension).

Modeled on OrgSettingsRepository, NOT ScopedRepository: the policy table is
org-only and has no workspace_id column.
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models.sandbox_policy import SandboxPolicy


class SandboxPolicyRepository:
    def __init__(self, session: AsyncSession, *, org_id: str) -> None:
        self.session = session
        self.org_id = org_id

    async def get(self) -> SandboxPolicy | None:
        """Return the org-default row (scope_workspace_id IS NULL)."""
        stmt = (
            select(SandboxPolicy)
            .where(SandboxPolicy.org_id == self.org_id)  # type: ignore[arg-type]
            .where(SandboxPolicy.scope_workspace_id.is_(None))  # type: ignore[union-attr]
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def upsert(
        self,
        *,
        default_image: str,
        network_rules: list[dict[str, Any]] | None,
        command_rules: list[dict[str, Any]] | None,
        network_default_action: str,
        egress_proxy: str | None,
    ) -> SandboxPolicy:
        """Upsert the org-default policy row (scope_workspace_id=NULL).

        v2 will add ``upsert_for_workspace(workspace_id, ...)`` for override
        rows without touching this method.
        """
        existing = await self.get()
        if existing is not None:
            existing.default_image = default_image
            existing.network_rules = network_rules
            existing.command_rules = command_rules
            existing.network_default_action = network_default_action
            existing.egress_proxy = egress_proxy
            await self.session.commit()
            await self.session.refresh(existing)
            return existing
        row = SandboxPolicy(
            org_id=self.org_id,
            scope_workspace_id=None,  # v1 only writes org-default rows
            default_image=default_image,
            network_rules=network_rules,
            command_rules=command_rules,
            network_default_action=network_default_action,
            egress_proxy=egress_proxy,
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row
