"""External identity repository — user-scoped or connection-scoped lookups."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models.external_identity import ExternalIdentity


class ExternalIdentityRepository:
    """Repository for external identity links (SSO / social).

    Not org-scoped: identities are looked up either by their external
    coordinates (provider_type + provider_id + external_id) during the
    SSO callback, or by the cubebox user id for account-management views.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find_by_external(
        self,
        *,
        provider_type: str,
        provider_id: str,
        external_id: str,
    ) -> ExternalIdentity | None:
        """Look up an identity by its external coordinates."""
        stmt = select(ExternalIdentity).where(
            ExternalIdentity.provider_type == provider_type,  # type: ignore[arg-type]
            ExternalIdentity.provider_id == provider_id,  # type: ignore[arg-type]
            ExternalIdentity.external_id == external_id,  # type: ignore[arg-type]
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_id(self, identity_id: str) -> ExternalIdentity | None:
        stmt = select(ExternalIdentity).where(
            ExternalIdentity.id == identity_id  # type: ignore[arg-type]
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_by_user(self, user_id: str) -> list[ExternalIdentity]:
        stmt = select(ExternalIdentity).where(
            ExternalIdentity.user_id == user_id  # type: ignore[arg-type]
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_connection(self, sso_connection_id: str) -> list[ExternalIdentity]:
        """List all identities linked via a specific SSO connection."""
        stmt = select(ExternalIdentity).where(
            ExternalIdentity.provider_id == sso_connection_id  # type: ignore[arg-type]
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def add(self, identity: ExternalIdentity) -> ExternalIdentity:
        self.session.add(identity)
        await self.session.commit()
        await self.session.refresh(identity)
        return identity

    async def delete(self, identity_id: str) -> bool:
        stmt = select(ExternalIdentity).where(
            ExternalIdentity.id == identity_id  # type: ignore[arg-type]
        )
        identity = (await self.session.execute(stmt)).scalar_one_or_none()
        if identity is None:
            return False
        await self.session.delete(identity)
        await self.session.commit()
        return True
