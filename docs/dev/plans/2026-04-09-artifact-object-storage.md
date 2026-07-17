# Artifact Object Storage & Version History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upload artifact files to S3-compatible object storage during save_artifact, serve previews from object storage instead of sandbox, and add version history browsing UI.

**Architecture:** New `objectstore` module wraps `aioboto3` for async S3/OSS uploads and downloads. `ArtifactMiddleware` uploads files during `save_artifact`. API routes serve previews/downloads from object storage. New `artifact_versions` table tracks version history. Frontend version badge becomes clickable popover for version browsing.

**Tech Stack:** aioboto3, SQLModel, Alembic, FastAPI, React, Zustand, shadcn/ui Popover

**Note:** The user's `.env` has a typo: `CUBEPLEX_OBJECTSOTRE__ACCESS_SECRET` (missing an `R` in STORE). The config should use the correct spelling `objectstore.access_secret` and the user will need to fix the env var name.

---

### Task 1: Add aioboto3 Dependency

**Files:**
- Modify: `backend/pyproject.toml`

- [ ] **Step 1: Add aioboto3 to dependencies**

In `backend/pyproject.toml`, add `aioboto3` to the `dependencies` list:

```toml
    "uuid-utils>=0.14.1",
    "langgraph-checkpoint-mysql[aiomysql]>=3.0.0",
    "aioboto3>=13.0.0",
```

- [ ] **Step 2: Install dependencies**

Run: `cd /home/chris/cubeplex/backend && uv sync --all-extras`

- [ ] **Step 3: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock
git commit -m "chore: add aioboto3 dependency for object storage"
```

---

### Task 2: Add Object Storage Config

**Files:**
- Modify: `backend/config.yaml`

- [ ] **Step 1: Add objectstore section to config.yaml**

Add after the `database:` section at the end of `backend/config.yaml`:

```yaml
  # Object Storage Configuration (S3-compatible)
  objectstore:
    provider: "oss"  # "oss" or "s3"
    endpoint: "https://oss-cn-zhangjiakou.aliyuncs.com"
    bucket: "cubeplex-dev"
    region: "cn-zhangjiakou"
    access_key: ""
    access_secret: ""
```

- [ ] **Step 2: Commit**

```bash
git add backend/config.yaml
git commit -m "config: add objectstore section for S3/OSS"
```

---

### Task 3: Create Object Storage Client

**Files:**
- Create: `backend/cubeplex/objectstore/__init__.py`
- Create: `backend/cubeplex/objectstore/client.py`

- [ ] **Step 1: Create `__init__.py`**

```python
"""Object storage client for S3-compatible services (S3, OSS)."""

from cubeplex.objectstore.client import ObjectStoreClient, get_objectstore_client

__all__ = ["ObjectStoreClient", "get_objectstore_client"]
```

- [ ] **Step 2: Create `client.py`**

```python
"""Async S3-compatible object storage client using aioboto3."""

import mimetypes
from typing import TYPE_CHECKING

import aioboto3
from loguru import logger

from cubeplex.config import config

if TYPE_CHECKING:
    from types_aiobotocore_s3 import S3Client


class ObjectStoreClient:
    """Async client for S3-compatible object storage (S3/OSS)."""

    def __init__(self) -> None:
        self._session = aioboto3.Session()
        self._provider: str = config.get("objectstore.provider", "oss")
        self._endpoint: str = config.get("objectstore.endpoint", "")
        self._bucket: str = config.get("objectstore.bucket", "")
        self._region: str = config.get("objectstore.region", "")
        self._access_key: str = config.get("objectstore.access_key", "")
        self._access_secret: str = config.get("objectstore.access_secret", "")

    def _client_kwargs(self) -> dict[str, object]:
        """Build kwargs for the S3 client based on provider."""
        kwargs: dict[str, object] = {
            "service_name": "s3",
            "endpoint_url": self._endpoint,
            "region_name": self._region,
            "aws_access_key_id": self._access_key,
            "aws_secret_access_key": self._access_secret,
        }
        if self._provider == "oss":
            # OSS requires path-style addressing
            kwargs["config"] = aioboto3.Session().resource(
                "s3"
            ).__class__  # placeholder — see actual impl below
        return kwargs

    def _s3_config(self) -> "botocore.config.Config":  # noqa: F821
        """Build botocore Config for provider-specific settings."""
        import botocore.config

        if self._provider == "oss":
            return botocore.config.Config(s3={"addressing_style": "path"})
        return botocore.config.Config()

    async def _get_client(self) -> "S3Client":
        """Create an S3 client context. Caller must use `async with`."""
        return self._session.client(
            "s3",
            endpoint_url=self._endpoint,
            region_name=self._region,
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._access_secret,
            config=self._s3_config(),
        )

    async def upload_file(self, key: str, data: bytes, content_type: str | None = None) -> None:
        """Upload a single file to object storage."""
        extra: dict[str, str] = {}
        if content_type:
            extra["ContentType"] = content_type
        async with self._session.client(
            "s3",
            endpoint_url=self._endpoint,
            region_name=self._region,
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._access_secret,
            config=self._s3_config(),
        ) as s3:
            await s3.put_object(Bucket=self._bucket, Key=key, Body=data, **extra)
        logger.debug("Uploaded to object storage: {}", key)

    async def download_file(self, key: str) -> tuple[bytes, str]:
        """Download a single file. Returns (data, content_type)."""
        async with self._session.client(
            "s3",
            endpoint_url=self._endpoint,
            region_name=self._region,
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._access_secret,
            config=self._s3_config(),
        ) as s3:
            response = await s3.get_object(Bucket=self._bucket, Key=key)
            data = await response["Body"].read()
            content_type = response.get("ContentType", "application/octet-stream")
        return data, content_type

    async def list_objects(self, prefix: str) -> list[str]:
        """List all object keys under a prefix."""
        keys: list[str] = []
        async with self._session.client(
            "s3",
            endpoint_url=self._endpoint,
            region_name=self._region,
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._access_secret,
            config=self._s3_config(),
        ) as s3:
            paginator = s3.get_paginator("list_objects_v2")
            async for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    keys.append(obj["Key"])
        return keys

    async def upload_from_sandbox(
        self,
        sandbox: "Sandbox",  # noqa: F821
        sandbox_path: str,
        key_prefix: str,
    ) -> list[str]:
        """Upload a file or directory from sandbox to object storage.

        For a file: uploads single file at key_prefix/{filename}.
        For a directory: recursively uploads all files.

        Returns list of uploaded keys.
        """
        from cubeplex.sandbox.base import Sandbox

        assert isinstance(sandbox, Sandbox)

        # Check if path is directory
        is_dir = await sandbox.execute(f"test -d {sandbox_path!r}")
        if is_dir.exit_code == 0:
            return await self._upload_directory(sandbox, sandbox_path, key_prefix)
        else:
            return await self._upload_file(sandbox, sandbox_path, key_prefix)

    async def _upload_file(
        self, sandbox: "Sandbox", path: str, key_prefix: str  # noqa: F821
    ) -> list[str]:
        """Upload a single file from sandbox."""
        files = await sandbox.download([path])
        if not files:
            logger.warning("File not found in sandbox for upload: {}", path)
            return []
        _, content = files[0]
        filename = path.rsplit("/", 1)[-1]
        key = f"{key_prefix}{filename}"
        mime, _ = mimetypes.guess_type(filename)
        await self.upload_file(key, content, content_type=mime)
        return [key]

    async def _upload_directory(
        self, sandbox: "Sandbox", dir_path: str, key_prefix: str  # noqa: F821
    ) -> list[str]:
        """Upload all files in a sandbox directory recursively."""
        # List all files in directory
        result = await sandbox.execute(
            f"find {dir_path!r} -type f -printf '%P\\n'"
        )
        if result.exit_code != 0 or not result.output.strip():
            logger.warning("No files found in directory: {}", dir_path)
            return []

        relative_paths = [p for p in result.output.strip().split("\n") if p]
        uploaded_keys: list[str] = []

        for rel_path in relative_paths:
            abs_path = f"{dir_path.rstrip('/')}/{rel_path}"
            files = await sandbox.download([abs_path])
            if not files:
                continue
            _, content = files[0]
            key = f"{key_prefix}{rel_path}"
            mime, _ = mimetypes.guess_type(rel_path)
            await self.upload_file(key, content, content_type=mime)
            uploaded_keys.append(key)

        logger.info(
            "Uploaded {} files from sandbox directory {} to {}",
            len(uploaded_keys),
            dir_path,
            key_prefix,
        )
        return uploaded_keys


_client: ObjectStoreClient | None = None


def get_objectstore_client() -> ObjectStoreClient:
    """Get singleton ObjectStoreClient instance."""
    global _client  # noqa: PLW0603
    if _client is None:
        _client = ObjectStoreClient()
    return _client
```

- [ ] **Step 3: Run type check**

Run: `cd /home/chris/cubeplex/backend && uv run mypy cubeplex/objectstore/`
Expected: pass (or only minor issues from unresolvable TYPE_CHECKING imports)

- [ ] **Step 4: Commit**

```bash
git add backend/cubeplex/objectstore/
git commit -m "feat: add async object storage client for S3/OSS"
```

---

### Task 4: Create ArtifactVersion Model and Migration

**Files:**
- Create: `backend/cubeplex/models/artifact_version.py`
- Modify: `backend/cubeplex/models/__init__.py`
- Create: `backend/alembic/versions/d8e9f0a1b2c3_create_artifact_versions_table.py`

- [ ] **Step 1: Create ArtifactVersion model**

Create `backend/cubeplex/models/artifact_version.py`:

```python
"""ArtifactVersion model — tracks version history for artifacts."""

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel
from uuid_utils import uuid7


class ArtifactVersion(SQLModel, table=True):
    """Snapshot of artifact metadata at a specific version."""

    __tablename__ = "artifact_versions"

    id: str = Field(default_factory=lambda: str(uuid7()), primary_key=True)
    artifact_id: str = Field(foreign_key="artifacts.id", index=True)
    version: int
    name: str = Field(max_length=255)
    description: str | None = Field(default=None, max_length=1024)
    path: str = Field(max_length=1024)
    entry_file: str | None = Field(default=None, max_length=255)
    mime_type: str | None = Field(default=None, max_length=128)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, object]:
        """Convert to API-friendly dict."""
        return {
            "id": self.id,
            "artifact_id": self.artifact_id,
            "version": self.version,
            "name": self.name,
            "description": self.description,
            "path": self.path,
            "entry_file": self.entry_file,
            "mime_type": self.mime_type,
            "created_at": self.created_at.isoformat(),
        }
```

- [ ] **Step 2: Update models `__init__.py`**

In `backend/cubeplex/models/__init__.py`:

```python
"""Data models."""

from cubeplex.models.artifact import Artifact
from cubeplex.models.artifact_version import ArtifactVersion
from cubeplex.models.conversation import Conversation
from cubeplex.models.user_sandbox import UserSandbox

__all__ = ["Artifact", "ArtifactVersion", "Conversation", "UserSandbox"]
```

- [ ] **Step 3: Create Alembic migration**

Create `backend/alembic/versions/d8e9f0a1b2c3_create_artifact_versions_table.py`:

```python
"""create_artifact_versions_table

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
Create Date: 2026-04-09 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = "d8e9f0a1b2c3"
down_revision: Union[str, None] = "c7d8e9f0a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "artifact_versions",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
        sa.Column(
            "artifact_id",
            sqlmodel.sql.sqltypes.AutoString(length=255),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=False),
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(length=1024), nullable=True),
        sa.Column("path", sqlmodel.sql.sqltypes.AutoString(length=1024), nullable=False),
        sa.Column("entry_file", sqlmodel.sql.sqltypes.AutoString(length=255), nullable=True),
        sa.Column("mime_type", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_artifact_versions_artifact_id", "artifact_versions", ["artifact_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_artifact_versions_artifact_id", table_name="artifact_versions")
    op.drop_table("artifact_versions")
```

- [ ] **Step 4: Run migration**

Run: `cd /home/chris/cubeplex/backend && uv run alembic upgrade head`

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/models/artifact_version.py backend/cubeplex/models/__init__.py backend/alembic/versions/d8e9f0a1b2c3_create_artifact_versions_table.py
git commit -m "feat: add artifact_versions table for version history"
```

---

### Task 5: Add ArtifactVersion Repository

**Files:**
- Modify: `backend/cubeplex/repositories/artifact.py`
- Modify: `backend/cubeplex/repositories/__init__.py`

- [ ] **Step 1: Add ArtifactVersionRepository to artifact.py**

Append to `backend/cubeplex/repositories/artifact.py` after the `ArtifactRepository` class:

```python
from cubeplex.models.artifact_version import ArtifactVersion


class ArtifactVersionRepository:
    """Repository for ArtifactVersion read/write operations."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

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
        self.session.add(av)
        await self.session.commit()
        await self.session.refresh(av)
        return av

    async def list_by_artifact(self, artifact_id: str) -> list[ArtifactVersion]:
        """List all versions for an artifact, newest first."""
        stmt = (
            select(ArtifactVersion)
            .where(ArtifactVersion.artifact_id == artifact_id)  # type: ignore[arg-type]
            .order_by(ArtifactVersion.version.desc())  # type: ignore[union-attr]
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_version(self, artifact_id: str, version: int) -> ArtifactVersion | None:
        """Get a specific version of an artifact."""
        stmt = select(ArtifactVersion).where(
            ArtifactVersion.artifact_id == artifact_id,  # type: ignore[arg-type]
            ArtifactVersion.version == version,  # type: ignore[arg-type]
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
```

- [ ] **Step 2: Update repositories `__init__.py`**

```python
"""Repository layer."""

from cubeplex.repositories.artifact import ArtifactRepository, ArtifactVersionRepository
from cubeplex.repositories.conversation import ConversationRepository
from cubeplex.repositories.user_sandbox import UserSandboxRepository

__all__ = [
    "ArtifactRepository",
    "ArtifactVersionRepository",
    "ConversationRepository",
    "UserSandboxRepository",
]
```

- [ ] **Step 3: Run type check**

Run: `cd /home/chris/cubeplex/backend && uv run mypy cubeplex/repositories/artifact.py`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/cubeplex/repositories/artifact.py backend/cubeplex/repositories/__init__.py
git commit -m "feat: add ArtifactVersionRepository"
```

---

### Task 6: Modify ArtifactMiddleware to Upload to Object Storage

**Files:**
- Modify: `backend/cubeplex/middleware/artifacts.py`

- [ ] **Step 1: Update the `_save_artifact` inner function**

Replace the `_save_artifact` inner function in `_create_save_artifact_tool` (lines 55-111 of `backend/cubeplex/middleware/artifacts.py`) with:

```python
    async def _save_artifact(
        name: str,
        artifact_type: str,
        path: str,
        entry_file: str | None = None,
        description: str | None = None,
        artifact_id: str | None = None,
    ) -> str:
        # 1. Validate path exists in sandbox
        result = await sandbox.execute(f"test -e {shlex.quote(path)}")
        if result.exit_code is not None and result.exit_code != 0:
            return json.dumps({"error": f"Path not found in sandbox: {path}"})

        # 2. Guess MIME type
        mime_type = _guess_mime_type(path, entry_file)

        # 3. Write to DB using independent session
        from cubeplex.db.engine import async_session_maker
        from cubeplex.repositories import ArtifactRepository, ArtifactVersionRepository

        async with async_session_maker() as session:
            repo = ArtifactRepository(session)
            version_repo = ArtifactVersionRepository(session)

            if artifact_id:
                artifact = await repo.update(
                    artifact_id,
                    name=name,
                    artifact_type=artifact_type,
                    path=path,
                    entry_file=entry_file,
                    mime_type=mime_type,
                    description=description,
                )
                if not artifact:
                    return json.dumps({"error": f"Artifact not found: {artifact_id}"})
                action = "updated"
            else:
                artifact = await repo.create(
                    conversation_id=conversation_id,
                    name=name,
                    artifact_type=artifact_type,
                    path=path,
                    entry_file=entry_file,
                    mime_type=mime_type,
                    description=description,
                )
                action = "created"

            # 4. Create version snapshot
            await version_repo.create(
                artifact_id=artifact.id,
                version=artifact.version,
                name=name,
                description=description,
                path=path,
                entry_file=entry_file,
                mime_type=mime_type,
            )

        # 5. Upload to object storage
        try:
            from cubeplex.objectstore import get_objectstore_client

            store = get_objectstore_client()
            key_prefix = (
                f"artifacts/{conversation_id}/{artifact.id}/v{artifact.version}/"
            )
            await store.upload_from_sandbox(sandbox, path, key_prefix)
        except Exception:
            logger.exception(
                "Failed to upload artifact {} to object storage (non-fatal)", artifact.id
            )

        logger.info(
            "Artifact {}: id={}, name={}, type={}, version={}",
            action,
            artifact.id,
            artifact.name,
            artifact.artifact_type,
            artifact.version,
        )

        return json.dumps({"action": action, "artifact": artifact.to_dict()})
```

- [ ] **Step 2: Run type check**

Run: `cd /home/chris/cubeplex/backend && uv run mypy cubeplex/middleware/artifacts.py`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add backend/cubeplex/middleware/artifacts.py
git commit -m "feat: upload artifacts to object storage during save_artifact"
```

---

### Task 7: Modify API Routes to Serve from Object Storage

**Files:**
- Modify: `backend/cubeplex/api/routes/v1/artifacts.py`

- [ ] **Step 1: Rewrite the artifacts routes file**

Replace the full contents of `backend/cubeplex/api/routes/v1/artifacts.py` with:

```python
"""Artifacts API routes."""

import mimetypes
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.db import get_session
from cubeplex.objectstore import get_objectstore_client
from cubeplex.repositories import ArtifactRepository, ArtifactVersionRepository

router = APIRouter(prefix="/conversations/{conversation_id}/artifacts", tags=["artifacts"])


@router.get("")
async def list_artifacts(
    conversation_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    """List all artifacts for a conversation."""
    repo = ArtifactRepository(session)
    artifacts = await repo.list_by_conversation(conversation_id)
    return {
        "artifacts": [a.to_dict() for a in artifacts],
        "total": len(artifacts),
    }


@router.get("/{artifact_id}")
async def get_artifact(
    conversation_id: str,
    artifact_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    """Get a single artifact by ID."""
    repo = ArtifactRepository(session)
    artifact = await repo.get_by_id(artifact_id)
    if not artifact or artifact.conversation_id != conversation_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Artifact {artifact_id} not found",
        )
    return artifact.to_dict()


@router.get("/{artifact_id}/versions")
async def list_artifact_versions(
    conversation_id: str,
    artifact_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    """List all versions of an artifact."""
    # Verify artifact belongs to conversation
    repo = ArtifactRepository(session)
    artifact = await repo.get_by_id(artifact_id)
    if not artifact or artifact.conversation_id != conversation_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Artifact {artifact_id} not found",
        )

    version_repo = ArtifactVersionRepository(session)
    versions = await version_repo.list_by_artifact(artifact_id)
    return {
        "versions": [v.to_dict() for v in versions],
        "total": len(versions),
    }


@router.get("/{artifact_id}/download")
async def download_artifact(
    conversation_id: str,
    artifact_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    version: int | None = Query(default=None, description="Version to download"),
) -> Response:
    """Download an artifact file from object storage."""
    repo = ArtifactRepository(session)
    artifact = await repo.get_by_id(artifact_id)
    if not artifact or artifact.conversation_id != conversation_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Artifact {artifact_id} not found",
        )

    target_version = version or artifact.version
    store = get_objectstore_client()
    prefix = f"artifacts/{conversation_id}/{artifact_id}/v{target_version}/"

    try:
        keys = await store.list_objects(prefix)
        if not keys:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Artifact files not found in storage",
            )

        if len(keys) == 1:
            # Single file — download directly
            data, content_type = await store.download_file(keys[0])
            filename = keys[0].rsplit("/", 1)[-1]
            return Response(
                content=data,
                media_type=content_type,
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        else:
            # Multiple files — tar them
            import io
            import tarfile

            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w") as tar:
                for key in keys:
                    rel_path = key[len(prefix):]
                    data, _ = await store.download_file(key)
                    info = tarfile.TarInfo(name=rel_path)
                    info.size = len(data)
                    tar.addfile(info, io.BytesIO(data))
            buf.seek(0)
            filename = f"{artifact.name}.tar"
            return Response(
                content=buf.read(),
                media_type="application/x-tar",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error downloading artifact from storage: {}", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to download artifact",
        ) from None


@router.get("/{artifact_id}/preview/{file_path:path}")
async def preview_artifact_file(
    conversation_id: str,
    artifact_id: str,
    file_path: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    version: int | None = Query(default=None, description="Version to preview"),
) -> Response:
    """Serve a single file from an artifact for iframe preview."""
    repo = ArtifactRepository(session)
    artifact = await repo.get_by_id(artifact_id)
    if not artifact or artifact.conversation_id != conversation_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Artifact {artifact_id} not found",
        )

    # Prevent path traversal
    if ".." in file_path or file_path.startswith("/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file path",
        )

    target_version = version or artifact.version
    store = get_objectstore_client()
    key = f"artifacts/{conversation_id}/{artifact_id}/v{target_version}/{file_path}"

    try:
        data, content_type = await store.download_file(key)
    except Exception as e:
        error_str = str(e)
        if "NoSuchKey" in error_str or "404" in error_str:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"File not found: {file_path}",
            ) from None
        logger.error("Error serving preview from storage: {}", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to serve preview file",
        ) from None

    # Override content type from file extension if more specific
    mime, _ = mimetypes.guess_type(file_path)
    if mime:
        content_type = mime

    return Response(
        content=data,
        media_type=content_type,
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )
```

Note: Preview responses use `Cache-Control: public, max-age=31536000, immutable` because versioned content never changes (a new version creates a new path).

- [ ] **Step 2: Run type check**

Run: `cd /home/chris/cubeplex/backend && uv run mypy cubeplex/api/routes/v1/artifacts.py`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add backend/cubeplex/api/routes/v1/artifacts.py
git commit -m "feat: serve artifact preview/download from object storage, add versions endpoint"
```

---

### Task 8: Frontend — Add ArtifactVersion Type and API

**Files:**
- Modify: `frontend/packages/core/src/types/artifact.ts`
- Modify: `frontend/packages/core/src/api/conversations.ts`

- [ ] **Step 1: Add ArtifactVersion type**

Append to `frontend/packages/core/src/types/artifact.ts`:

```typescript
export interface ArtifactVersion {
  id: string
  artifact_id: string
  version: number
  name: string
  description?: string | null
  path: string
  entry_file?: string | null
  mime_type?: string | null
  created_at: string
}
```

- [ ] **Step 2: Export ArtifactVersion from types index**

Check `frontend/packages/core/src/types/index.ts` and ensure `ArtifactVersion` is exported. If the file re-exports from `artifact.ts` with `export *`, no change needed. Otherwise add:

```typescript
export type { ArtifactVersion } from './artifact'
```

- [ ] **Step 3: Add listArtifactVersions to API**

Append to `frontend/packages/core/src/api/conversations.ts`:

```typescript
export async function listArtifactVersions(
  client: ApiClient,
  conversationId: string,
  artifactId: string,
): Promise<ArtifactVersion[]> {
  const url = `/api/v1/conversations/${conversationId}/artifacts/${artifactId}/versions`
  const res = await client.get(url)
  if (!res.ok) throw await toApiError(res)
  const data = await res.json() as { versions?: ArtifactVersion[] }
  return data.versions || []
}
```

Add the `ArtifactVersion` import at the top of the file:

```typescript
import type { Artifact, ArtifactVersion, Conversation, Message } from '../types'
```

- [ ] **Step 4: Build core and type check**

Run: `cd /home/chris/cubeplex/frontend && pnpm --filter @cubeplex/core build && pnpm type-check`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/core/src/types/artifact.ts frontend/packages/core/src/api/conversations.ts frontend/packages/core/src/types/index.ts
git commit -m "feat: add ArtifactVersion type and listArtifactVersions API"
```

---

### Task 9: Frontend — Update Artifact Store with Version State

**Files:**
- Modify: `frontend/packages/core/src/stores/artifactStore.ts`

- [ ] **Step 1: Add version state and actions**

Replace the full contents of `frontend/packages/core/src/stores/artifactStore.ts`:

```typescript
// frontend/packages/core/src/stores/artifactStore.ts
import { create } from 'zustand'
import type { ApiClient } from '../api/client'
import { listArtifacts, listArtifactVersions } from '../api/conversations'
import type { Artifact, ArtifactVersion } from '../types'

export interface ArtifactStore {
  /** Artifacts indexed by conversationId, then by artifactId */
  artifacts: Record<string, Record<string, Artifact>>

  /** Loading state per conversation */
  loading: Record<string, boolean>

  /** Version lists per artifact (cached after first fetch) */
  versions: Record<string, ArtifactVersion[]>

  /** Selected version per artifact (null = latest) */
  selectedVersion: Record<string, number | null>

  /** Add or update an artifact for a conversation */
  addOrUpdate: (conversationId: string, artifact: Artifact) => void

  /** Load all artifacts for a conversation from the API */
  loadArtifacts: (client: ApiClient, conversationId: string) => Promise<void>

  /** Check if artifacts are loading for a conversation */
  isLoading: (conversationId: string) => boolean

  /** Get all artifacts for a conversation */
  getArtifacts: (conversationId: string) => Artifact[]

  /** Clear artifacts for a conversation */
  clearConversation: (conversationId: string) => void

  /** Load version list for an artifact */
  loadVersions: (client: ApiClient, conversationId: string, artifactId: string) => Promise<void>

  /** Select a version for preview (null = latest) */
  selectVersion: (artifactId: string, version: number | null) => void

  /** Get selected version for an artifact */
  getSelectedVersion: (artifactId: string) => number | null
}

export const useArtifactStore = create<ArtifactStore>((set, get) => ({
  artifacts: {},
  loading: {},
  versions: {},
  selectedVersion: {},

  addOrUpdate: (conversationId, artifact) =>
    set((state) => ({
      artifacts: {
        ...state.artifacts,
        [conversationId]: {
          ...state.artifacts[conversationId],
          [artifact.id]: artifact,
        },
      },
    })),

  async loadArtifacts(client, conversationId) {
    set((s) => ({ loading: { ...s.loading, [conversationId]: true } }))
    try {
      const artifacts = await listArtifacts(client, conversationId)
      if (artifacts.length === 0) return
      const map: Record<string, Artifact> = {}
      for (const a of artifacts) {
        map[a.id] = a
      }
      set((state) => ({
        artifacts: {
          ...state.artifacts,
          [conversationId]: { ...state.artifacts[conversationId], ...map },
        },
      }))
    } catch {
      // Artifacts are non-critical; silently ignore load failures
    } finally {
      set((s) => ({ loading: { ...s.loading, [conversationId]: false } }))
    }
  },

  isLoading: (conversationId) => !!get().loading[conversationId],

  getArtifacts: (conversationId) => {
    const conv = get().artifacts[conversationId]
    return conv ? Object.values(conv) : []
  },

  clearConversation: (conversationId) =>
    set((state) => {
      const { [conversationId]: _, ...rest } = state.artifacts
      return { artifacts: rest }
    }),

  async loadVersions(client, conversationId, artifactId) {
    // Skip if already loaded
    if (get().versions[artifactId]) return
    try {
      const versions = await listArtifactVersions(client, conversationId, artifactId)
      set((state) => ({
        versions: { ...state.versions, [artifactId]: versions },
      }))
    } catch {
      // Non-critical
    }
  },

  selectVersion: (artifactId, version) =>
    set((state) => ({
      selectedVersion: { ...state.selectedVersion, [artifactId]: version },
    })),

  getSelectedVersion: (artifactId) => get().selectedVersion[artifactId] ?? null,
}))
```

- [ ] **Step 2: Export new types from core index if needed**

Ensure `ArtifactStore` types are exported from `frontend/packages/core/src/index.ts`. The `useArtifactStore` is likely already exported. Check and add if needed.

- [ ] **Step 3: Build core and type check**

Run: `cd /home/chris/cubeplex/frontend && pnpm --filter @cubeplex/core build && pnpm type-check`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/core/src/stores/artifactStore.ts
git commit -m "feat: add version state and actions to artifact store"
```

---

### Task 10: Frontend — Version Popover in ArtifactPanel

**Files:**
- Modify: `frontend/packages/web/components/panel/artifact/ArtifactPanel.tsx`

- [ ] **Step 1: Update ArtifactPanel with version popover and version-aware preview URLs**

Replace the full contents of `frontend/packages/web/components/panel/artifact/ArtifactPanel.tsx`:

```tsx
'use client'

import { useState, useEffect } from 'react'
import dynamic from 'next/dynamic'
import { useArtifactStore, usePanelStore } from '@cubeplex/core'
import type { Artifact, ArtifactVersion } from '@cubeplex/core'
import { X, Download, ChevronDown } from 'lucide-react'
import { getArtifactIcon } from './artifactIcons'
import { PreviewLoading } from './PreviewLoading'
import { HtmlPreview } from './HtmlPreview'
import { ImagePreview } from './ImagePreview'
import { CodePreview } from './CodePreview'
import { DocumentPreview } from './DocumentPreview'
import { DataPreview } from './DataPreview'
import { FallbackPreview } from './FallbackPreview'
import { useApiClient } from '@/hooks/useApiClient'
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover'

const PdfPreview = dynamic(
  () => import('./PdfPreview').then(m => m.PdfPreview),
  {
    ssr: false,
    loading: () => <PreviewLoading />,
  },
)

function isPdf(artifact: Artifact): boolean {
  if (artifact.mime_type === 'application/pdf') return true
  const filename = artifact.entry_file || artifact.path.split('/').pop() || ''
  return /\.pdf$/i.test(filename)
}

function formatRelativeTime(dateStr: string): string {
  const date = new Date(dateStr)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffMin = Math.floor(diffMs / 60000)
  if (diffMin < 1) return 'just now'
  if (diffMin < 60) return `${diffMin}m ago`
  const diffHr = Math.floor(diffMin / 60)
  if (diffHr < 24) return `${diffHr}h ago`
  const diffDay = Math.floor(diffHr / 24)
  return `${diffDay}d ago`
}

function VersionPopover({
  artifact,
  versions,
  selectedVersion,
  onSelectVersion,
}: {
  artifact: Artifact
  versions: ArtifactVersion[]
  selectedVersion: number | null
  onSelectVersion: (version: number | null) => void
}) {
  const [open, setOpen] = useState(false)
  const currentVersion = selectedVersion ?? artifact.version

  if (artifact.version <= 1) {
    return (
      <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[10px]
        text-muted-foreground">
        v{artifact.version}
      </span>
    )
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[10px]
            text-muted-foreground hover:bg-muted/80 transition-colors flex items-center gap-0.5"
        >
          v{currentVersion}
          <ChevronDown className="size-2.5" />
        </button>
      </PopoverTrigger>
      <PopoverContent className="w-56 p-1" align="end">
        <div className="max-h-48 overflow-y-auto">
          {versions.map((v) => (
            <button
              key={v.id}
              onClick={() => {
                onSelectVersion(v.version === artifact.version ? null : v.version)
                setOpen(false)
              }}
              className={`w-full text-left px-2 py-1.5 rounded text-xs flex items-center
                justify-between hover:bg-muted/50 transition-colors
                ${v.version === currentVersion ? 'bg-muted' : ''}`}
            >
              <span className="flex items-center gap-1.5">
                <span className="font-medium">v{v.version}</span>
                {v.name !== artifact.name && (
                  <span className="text-muted-foreground truncate max-w-[100px]">
                    {v.name}
                  </span>
                )}
              </span>
              <span className="text-muted-foreground text-[10px]">
                {formatRelativeTime(v.created_at)}
              </span>
            </button>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  )
}

function ArtifactPanelHeader({
  artifact,
  versions,
  selectedVersion,
  onSelectVersion,
  onClose,
}: {
  artifact: Artifact
  versions: ArtifactVersion[]
  selectedVersion: number | null
  onSelectVersion: (version: number | null) => void
  onClose: () => void
}) {
  const Icon = getArtifactIcon(artifact)
  const versionParam = selectedVersion ? `?version=${selectedVersion}` : ''
  const downloadUrl =
    `/api/v1/conversations/${artifact.conversation_id}/artifacts/${artifact.id}/download${versionParam}`

  return (
    <header className="h-11 border-b border-border flex items-center gap-2 px-4 shrink-0 bg-card">
      <Icon className="size-3.5 text-primary shrink-0" />
      <span className="text-sm font-medium text-foreground truncate flex-1">
        {artifact.name}
      </span>
      <VersionPopover
        artifact={artifact}
        versions={versions}
        selectedVersion={selectedVersion}
        onSelectVersion={onSelectVersion}
      />
      <span className="flex items-center gap-1">
        <a
          href={downloadUrl}
          className="p-1 rounded hover:bg-muted/50 transition-colors"
          title="Download"
        >
          <Download className="size-3.5 text-muted-foreground" />
        </a>
        <button
          onClick={onClose}
          className="p-1 rounded hover:bg-muted/50 transition-colors"
          title="Close"
        >
          <X className="size-3.5 text-muted-foreground" />
        </button>
      </span>
    </header>
  )
}

function PreviewContent({ artifact, version }: { artifact: Artifact; version: number | null }) {
  // Route PDFs to PdfPreview regardless of artifact_type
  if (isPdf(artifact)) {
    return <PdfPreview artifact={artifact} version={version} />
  }

  switch (artifact.artifact_type) {
    case 'website':
      return <HtmlPreview artifact={artifact} version={version} />
    case 'image':
      return <ImagePreview artifact={artifact} version={version} />
    case 'code':
      return <CodePreview artifact={artifact} version={version} />
    case 'document':
      return <DocumentPreview artifact={artifact} version={version} />
    case 'data':
      return <DataPreview artifact={artifact} version={version} />
    default:
      return <FallbackPreview artifact={artifact} version={version} />
  }
}

export function ArtifactPanel() {
  const view = usePanelStore(s => s.view)
  const close = usePanelStore(s => s.close)
  const artifacts = useArtifactStore(s => s.artifacts)
  const versions = useArtifactStore(s => s.versions)
  const selectedVersion = useArtifactStore(s => s.selectedVersion)
  const loadVersions = useArtifactStore(s => s.loadVersions)
  const selectVersion = useArtifactStore(s => s.selectVersion)
  const client = useApiClient()

  if (view.type !== 'artifact') return null

  const artifact = artifacts[view.conversationId]?.[view.artifactId]
  if (!artifact) return null

  const artifactVersions = versions[artifact.id] || []
  const currentSelectedVersion = selectedVersion[artifact.id] ?? null

  // Load versions when panel opens for an artifact with version > 1
  useEffect(() => {
    if (artifact.version > 1 && client) {
      loadVersions(client, artifact.conversation_id, artifact.id)
    }
  }, [artifact.id, artifact.version, client, loadVersions])

  return (
    <div className="flex flex-col h-full bg-background">
      <ArtifactPanelHeader
        artifact={artifact}
        versions={artifactVersions}
        selectedVersion={currentSelectedVersion}
        onSelectVersion={(v) => selectVersion(artifact.id, v)}
        onClose={close}
      />
      <div className="flex-1 overflow-hidden">
        <PreviewContent artifact={artifact} version={currentSelectedVersion} />
      </div>
    </div>
  )
}
```

Note: This assumes `useApiClient` hook exists at `@/hooks/useApiClient` and `Popover` components exist from shadcn/ui. Verify these exist before implementation. If `useApiClient` doesn't exist, check how other components obtain the API client.

- [ ] **Step 2: Type check**

Run: `cd /home/chris/cubeplex/frontend && pnpm type-check`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/components/panel/artifact/ArtifactPanel.tsx
git commit -m "feat: add version popover to artifact panel header"
```

---

### Task 11: Frontend — Update Preview Components to Accept Version Prop

**Files:**
- Modify: `frontend/packages/web/components/panel/artifact/HtmlPreview.tsx`
- Modify: `frontend/packages/web/components/panel/artifact/ImagePreview.tsx`
- Modify: `frontend/packages/web/components/panel/artifact/CodePreview.tsx`
- Modify: `frontend/packages/web/components/panel/artifact/DocumentPreview.tsx`
- Modify: `frontend/packages/web/components/panel/artifact/DataPreview.tsx`
- Modify: `frontend/packages/web/components/panel/artifact/PdfPreview.tsx`
- Modify: `frontend/packages/web/components/panel/artifact/FallbackPreview.tsx`

All preview components need the same change: accept an optional `version` prop and append `?version=N` to preview URLs when version is not null.

- [ ] **Step 1: Create a shared URL helper**

Create a small helper function. Add to the top of each preview component, or better, add to a shared file. Since there's already `artifactIcons.ts`, create a `previewUtils.ts` in the same directory:

Create `frontend/packages/web/components/panel/artifact/previewUtils.ts`:

```typescript
import type { Artifact } from '@cubeplex/core'

export function buildPreviewUrl(
  artifact: Artifact,
  filePath: string,
  version: number | null,
): string {
  const base =
    `/api/v1/conversations/${artifact.conversation_id}/artifacts/${artifact.id}/preview/${filePath}`
  return version != null ? `${base}?version=${version}` : base
}
```

- [ ] **Step 2: Update HtmlPreview**

In `HtmlPreview.tsx`, update the component to accept `version` prop and use `buildPreviewUrl`:

Change the props from `{ artifact: Artifact }` to `{ artifact: Artifact; version: number | null }`.

Change the preview URL construction from:
```typescript
const previewUrl =
  `/api/v1/conversations/${artifact.conversation_id}/artifacts/${artifact.id}/preview/${entryFile}`
```
to:
```typescript
import { buildPreviewUrl } from './previewUtils'
// ...
const previewUrl = buildPreviewUrl(artifact, entryFile, version)
```

Also add `version` to the iframe `key` prop so it re-renders on version change:
```tsx
<iframe key={`${artifact.id}-${version}`} src={previewUrl} ... />
```

- [ ] **Step 3: Update ImagePreview**

Same pattern: accept `version` prop, use `buildPreviewUrl(artifact, filename, version)`. Add `version` to image `key` prop.

- [ ] **Step 4: Update CodePreview**

Accept `version` prop, use `buildPreviewUrl`. Add `version` to the `useEffect` dependency array so it re-fetches on version change.

- [ ] **Step 5: Update DocumentPreview**

Accept `version` prop, use `buildPreviewUrl`. Add `version` to `useEffect` deps.

- [ ] **Step 6: Update DataPreview**

Accept `version` prop, use `buildPreviewUrl`. Add `version` to `useEffect` deps.

- [ ] **Step 7: Update PdfPreview**

Accept `version` prop, use `buildPreviewUrl`. Add `version` to effect deps / key prop.

- [ ] **Step 8: Update FallbackPreview**

Accept `version` prop. Update download URL to append `?version=N`:

```typescript
const versionParam = version != null ? `?version=${version}` : ''
const downloadUrl =
  `/api/v1/conversations/${artifact.conversation_id}/artifacts/${artifact.id}/download${versionParam}`
```

- [ ] **Step 9: Type check and build**

Run: `cd /home/chris/cubeplex/frontend && pnpm --filter @cubeplex/core build && pnpm type-check`
Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add frontend/packages/web/components/panel/artifact/
git commit -m "feat: pass version to all preview components, append ?version=N to URLs"
```

---

### Task 12: Backend Lint, Type Check, and Full Verification

**Files:** None (verification only)

- [ ] **Step 1: Run backend format and lint**

Run: `cd /home/chris/cubeplex/backend && make format && make lint`
Expected: PASS

- [ ] **Step 2: Run backend type check**

Run: `cd /home/chris/cubeplex/backend && make type-check`
Expected: PASS

- [ ] **Step 3: Run frontend type check**

Run: `cd /home/chris/cubeplex/frontend && pnpm type-check`
Expected: PASS

- [ ] **Step 4: Run frontend build**

Run: `cd /home/chris/cubeplex/frontend && pnpm build`
Expected: PASS

- [ ] **Step 5: Manual smoke test**

1. Start backend: `cd /home/chris/cubeplex/backend && python main.py`
2. Start frontend: `cd /home/chris/cubeplex/frontend && pnpm dev`
3. Create an artifact in chat (ask agent to create an HTML page)
4. Verify artifact preview loads from object storage
5. Update the artifact (ask agent to modify it)
6. Verify version badge shows v2
7. Click version badge — verify popover shows v1 and v2
8. Select v1 — verify old version renders correctly
9. Select v2 — verify latest version renders

- [ ] **Step 6: Commit any fixes from verification**

```bash
git add -A
git commit -m "fix: address issues from verification"
```
