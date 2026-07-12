"""Repository for EgressRef. Lookups by ref_hash are global (the exchange
caller is a sidecar, not an org-scoped user); writes/revokes are by sandbox."""

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models import EgressRef


class EgressRefRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, ref: EgressRef) -> EgressRef:
        self.session.add(ref)
        await self.session.commit()
        await self.session.refresh(ref)
        return ref

    async def get_valid_by_hash(self, ref_hash: str) -> EgressRef | None:
        now = datetime.now(UTC)
        stmt = select(EgressRef).where(
            EgressRef.ref_hash == ref_hash,  # type: ignore[arg-type]
            EgressRef.status == "valid",  # type: ignore[arg-type]
        )
        ref = (await self.session.execute(stmt)).scalar_one_or_none()
        if ref is None:
            return None
        exp = ref.expires_at
        if exp is not None:
            if exp.tzinfo is None:  # SQLite discards tz on round-trip
                exp = exp.replace(tzinfo=UTC)
            if exp < now:
                return None
        return ref

    async def revoke_for_sandbox(self, sandbox_id: str) -> None:
        await self.session.execute(
            update(EgressRef)
            .where(EgressRef.sandbox_id == sandbox_id)  # type: ignore[arg-type]
            .values(status="revoked")
        )
        await self.session.commit()

    async def extend_expiry_for_sandbox(self, sandbox_id: str, expires_at: datetime) -> None:
        """Push out expires_at for a sandbox's still-valid refs.

        Called when an active sandbox is touched so its placeholders don't expire
        mid-run on long sessions that outlive the original TTL.
        """
        await self.session.execute(
            update(EgressRef)
            .where(
                EgressRef.sandbox_id == sandbox_id,  # type: ignore[arg-type]
                EgressRef.status == "valid",  # type: ignore[arg-type]
            )
            .values(expires_at=expires_at)
        )
        await self.session.commit()
