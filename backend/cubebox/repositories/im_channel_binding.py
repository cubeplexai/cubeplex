"""IM channel binding repository — scoped CRUD for per-channel routing config."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models.im_channel_binding import IMChannelBinding
from cubebox.repositories.base import ScopedRepository


class IMChannelBindingRepository(ScopedRepository[IMChannelBinding]):
    model = IMChannelBinding

    def __init__(
        self,
        session: AsyncSession,
        *,
        org_id: str,
        workspace_id: str,
    ) -> None:
        super().__init__(session, org_id=org_id, workspace_id=workspace_id)

    async def create(
        self,
        *,
        account_id: str,
        channel_id: str,
        channel_name: str = "",
        mode: str = "isolated",
        sandbox_mode: str | None = None,
    ) -> IMChannelBinding:
        binding = IMChannelBinding(
            org_id=self.org_id,
            workspace_id=self.workspace_id,
            account_id=account_id,
            channel_id=channel_id,
            channel_name=channel_name,
            mode=mode,
            sandbox_mode=sandbox_mode,
        )
        self.session.add(binding)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ValueError(
                f"Channel {channel_id} is already bound to account {account_id}"
            ) from exc
        return binding

    async def get_by_account_channel(
        self,
        *,
        account_id: str,
        channel_id: str,
    ) -> IMChannelBinding | None:
        """Lookup by the unique (account_id, channel_id) pair — no scope filter."""
        stmt = select(IMChannelBinding).where(
            IMChannelBinding.account_id == account_id,  # type: ignore[arg-type]
            IMChannelBinding.channel_id == channel_id,  # type: ignore[arg-type]
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_by_account(
        self,
        *,
        account_id: str,
    ) -> list[IMChannelBinding]:
        """Scoped list for one account, newest first."""
        stmt = (
            self._scoped_select()
            .where(IMChannelBinding.account_id == account_id)
            .order_by(IMChannelBinding.created_at.desc())  # type: ignore[attr-defined]
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def update(
        self,
        *,
        binding_id: str,
        mode: str | None = None,
        sandbox_mode: Any = ...,
        channel_name: str | None = None,
    ) -> IMChannelBinding | None:
        """Update mutable fields on a binding. Returns None if not found (scoped)."""
        binding = await self.get(binding_id)
        if binding is None:
            return None
        if mode is not None:
            binding.mode = mode
        if sandbox_mode is not ...:
            binding.sandbox_mode = sandbox_mode
        if channel_name is not None:
            binding.channel_name = channel_name
        self.session.add(binding)
        await self.session.flush()
        return binding

    async def delete(self, id_: str) -> bool:
        """Scoped delete — flush, not commit."""
        obj = await self.get(id_)
        if obj is None:
            return False
        await self.session.delete(obj)
        await self.session.flush()
        return True

    async def set_topic_id(self, *, binding_id: str, topic_id: str) -> None:
        """Attach a topic to this binding (called by ingest after topic creation)."""
        binding = await self.get(binding_id)
        if binding is None:
            raise ValueError(f"Binding {binding_id} not found")
        binding.topic_id = topic_id
        self.session.add(binding)
        await self.session.flush()
