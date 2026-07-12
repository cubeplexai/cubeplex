"""Artifact repository."""

from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import delete, func, select

from cubeplex.models import Artifact
from cubeplex.models.artifact_version import ArtifactVersion
from cubeplex.repositories.base import ScopedRepository


class ArtifactRepository(ScopedRepository[Artifact]):
    """Repository for Artifact CRUD operations."""

    model = Artifact

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
        return await self.add(artifact)

    async def get_by_id(self, artifact_id: str) -> Artifact | None:
        """Get artifact by ID."""
        return await self.get(artifact_id)

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

    async def find_by_path(
        self,
        conversation_id: str,
        path: str,
    ) -> Artifact | None:
        """Find an existing artifact in a conversation by its sandbox path."""
        stmt = self._scoped_select().where(
            Artifact.conversation_id == conversation_id,
            Artifact.path == path,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_conversation(
        self,
        conversation_id: str,
    ) -> list[Artifact]:
        """List all artifacts for a conversation."""
        stmt = (
            self._scoped_select()
            .where(Artifact.conversation_id == conversation_id)
            .order_by(Artifact.created_at)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_workspace(
        self,
        *,
        accessible_conv_subq: Any,
        artifact_type: str | None = None,
        name_query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Artifact], int]:
        """List artifacts in the workspace restricted to accessible conversations.

        ``accessible_conv_subq`` is a single-column subquery of conversation
        IDs the caller may access (see
        ``ConversationRepository.accessible_id_subquery``). Optional filters:
        ``artifact_type`` (exact) and ``name_query`` (case-insensitive
        substring). Ordered newest-updated first. Returns ``(items, total)``.
        """
        stmt = self._scoped_select().where(
            cast(Any, Artifact.conversation_id).in_(accessible_conv_subq)
        )
        if artifact_type:
            stmt = stmt.where(Artifact.artifact_type == artifact_type)
        if name_query:
            stmt = stmt.where(cast(Any, Artifact.name).ilike(f"%{name_query}%"))

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await self.session.execute(count_stmt)).scalar_one()

        page_stmt = stmt.order_by(cast(Any, Artifact.updated_at).desc()).limit(limit).offset(offset)
        result = await self.session.execute(page_stmt)
        return list(result.scalars().all()), total

    async def delete_with_versions(self, artifact: Artifact) -> None:
        """Delete an already-loaded artifact and its version rows.

        The caller passes the loaded ``Artifact`` (already fetched for the
        access check) so this does not re-SELECT the same row.
        """
        await self.session.execute(
            delete(ArtifactVersion).where(
                cast(Any, ArtifactVersion.artifact_id) == artifact.id,
                cast(Any, ArtifactVersion.org_id) == self.org_id,
                cast(Any, ArtifactVersion.workspace_id) == self.workspace_id,
            )
        )
        await self.session.delete(artifact)
        await self.session.commit()


class ArtifactVersionRepository(ScopedRepository[ArtifactVersion]):
    """Repository for ArtifactVersion read/write operations."""

    model = ArtifactVersion

    async def create(
        self,
        *,
        artifact_id: str,
        version: int,
        name: str,
        description: str | None = None,
        path: str,
        entry_file: str | None = None,
        mime_type: str | None = None,
    ) -> ArtifactVersion:
        """Create a version snapshot."""
        av = ArtifactVersion(
            artifact_id=artifact_id,
            version=version,
            name=name,
            description=description,
            path=path,
            entry_file=entry_file,
            mime_type=mime_type,
        )
        return await self.add(av)

    async def list_by_artifact(self, artifact_id: str) -> list[ArtifactVersion]:
        """List all versions for an artifact, newest first."""
        stmt = (
            self._scoped_select()
            .where(ArtifactVersion.artifact_id == artifact_id)
            .order_by(ArtifactVersion.version.desc())  # type: ignore[attr-defined]
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_version(self, artifact_id: str, version: int) -> ArtifactVersion | None:
        """Get a specific version of an artifact."""
        stmt = self._scoped_select().where(
            ArtifactVersion.artifact_id == artifact_id,
            ArtifactVersion.version == version,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
