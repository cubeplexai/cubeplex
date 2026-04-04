"""Artifact repository."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models import Artifact


class ArtifactRepository:
    """Repository for Artifact CRUD operations."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        conversation_id: str,
        name: str,
        artifact_type: str,
        path: str,
        entry_file: str | None = None,
        mime_type: str | None = None,
        description: str | None = None,
    ) -> Artifact:
        """Create a new artifact."""
        artifact = Artifact(
            conversation_id=conversation_id,
            name=name,
            artifact_type=artifact_type,
            path=path,
            entry_file=entry_file,
            mime_type=mime_type,
            description=description,
        )
        self.session.add(artifact)
        await self.session.commit()
        await self.session.refresh(artifact)
        return artifact

    async def get_by_id(self, artifact_id: str) -> Artifact | None:
        """Get artifact by ID."""
        stmt = select(Artifact).where(Artifact.id == artifact_id)  # type: ignore[arg-type]
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def update(
        self,
        artifact_id: str,
        *,
        name: str | None = None,
        artifact_type: str | None = None,
        path: str | None = None,
        entry_file: str | None = None,
        mime_type: str | None = None,
        description: str | None = None,
    ) -> Artifact | None:
        """Update an existing artifact (bumps version)."""
        artifact = await self.get_by_id(artifact_id)
        if not artifact:
            return None

        if name is not None:
            artifact.name = name
        if artifact_type is not None:
            artifact.artifact_type = artifact_type
        if path is not None:
            artifact.path = path
        if entry_file is not None:
            artifact.entry_file = entry_file
        if mime_type is not None:
            artifact.mime_type = mime_type
        if description is not None:
            artifact.description = description

        artifact.version += 1
        artifact.updated_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(artifact)
        return artifact

    async def list_by_conversation(
        self,
        conversation_id: str,
    ) -> list[Artifact]:
        """List all artifacts for a conversation."""
        stmt = (
            select(Artifact)
            .where(Artifact.conversation_id == conversation_id)  # type: ignore[arg-type]
            .order_by(Artifact.created_at)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
