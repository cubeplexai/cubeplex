"""Model repository."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models.provider import Model


class ModelRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_by_provider(self, provider_id: str) -> list[Model]:
        stmt = (
            select(Model)
            .where(Model.provider_id == provider_id)  # type: ignore[arg-type]
            .where(Model.enabled)  # type: ignore[arg-type]
            .order_by(Model.model_id)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_all_for_provider(self, provider_id: str) -> list[Model]:
        """All models for a provider, including disabled (wizard models are enabled=false)."""
        stmt = (
            select(Model)
            .where(Model.provider_id == provider_id)  # type: ignore[arg-type]
            .order_by(Model.model_id)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get(self, model_db_id: str) -> Model | None:
        stmt = select(Model).where(Model.id == model_db_id)  # type: ignore[arg-type]
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_model_id(self, provider_id: str, model_id: str) -> Model | None:
        stmt = select(Model).where(
            Model.provider_id == provider_id,  # type: ignore[arg-type]
            Model.model_id == model_id,  # type: ignore[arg-type]
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def add(self, model: Model) -> Model:
        self.session.add(model)
        await self.session.commit()
        await self.session.refresh(model)
        return model

    async def update(self, model: Model) -> Model:
        await self.session.commit()
        await self.session.refresh(model)
        return model

    async def delete(self, model: Model) -> None:
        await self.session.delete(model)
        await self.session.commit()

    async def count_by_provider(self, provider_id: str) -> int:
        stmt = select(Model).where(Model.provider_id == provider_id)  # type: ignore[arg-type]
        result = await self.session.execute(stmt)
        return len(result.scalars().all())

    async def delete_by_provider(self, provider_id: str) -> None:
        from sqlalchemy import delete

        stmt = delete(Model).where(Model.provider_id == provider_id)  # type: ignore[arg-type]
        await self.session.execute(stmt)
        await self.session.flush()
