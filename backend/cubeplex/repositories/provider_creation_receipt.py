"""Organization-scoped idempotent creation outcomes."""

from datetime import UTC, datetime

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from cubeplex.models.provider_creation_receipt import ProviderCreationReceipt


class ProviderCreationReceiptRepository:
    def __init__(self, session: AsyncSession, *, org_id: str) -> None:
        self.session = session
        self.org_id = org_id

    async def lock_token(self, actor_user_id: str, token: str) -> None:
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:token, 0))"),
            {"token": f"provider-create:{self.org_id}:{actor_user_id}:{token}"},
        )

    async def get(self, actor_user_id: str, token: str) -> ProviderCreationReceipt | None:
        stmt = (
            select(ProviderCreationReceipt)
            .where(
                col(ProviderCreationReceipt.org_id) == self.org_id,
                ProviderCreationReceipt.created_by_user_id == actor_user_id,  # type: ignore[arg-type]
                ProviderCreationReceipt.creation_discovery_id == token,  # type: ignore[arg-type]
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def for_provider(self, provider_id: str) -> list[ProviderCreationReceipt]:
        stmt = (
            select(ProviderCreationReceipt)
            .where(
                col(ProviderCreationReceipt.org_id) == self.org_id,
                ProviderCreationReceipt.provider_id == provider_id,  # type: ignore[arg-type]
            )
            .order_by(ProviderCreationReceipt.id)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def cleanup_expired(self) -> None:
        expired = (
            select(col(ProviderCreationReceipt.id))
            .where(
                col(ProviderCreationReceipt.org_id) == self.org_id,
                col(ProviderCreationReceipt.expires_at) < datetime.now(UTC),
            )
            .order_by(ProviderCreationReceipt.id)
            .limit(100)
            .with_for_update(skip_locked=True)
        )
        await self.session.execute(
            delete(ProviderCreationReceipt).where(col(ProviderCreationReceipt.id).in_(expired))
        )
