"""Async S3/OSS object storage client using aioboto3."""

from __future__ import annotations

import posixpath
from typing import TYPE_CHECKING

import aioboto3
from botocore.config import Config as BotoConfig
from loguru import logger

from cubeplex.config import config

if TYPE_CHECKING:
    from cubeplex.sandbox.base import Sandbox


class ObjectStoreClient:
    """Async object storage client supporting S3 and Alibaba Cloud OSS.

    Each public method creates a fresh ``async with session.client(...)``
    context so the client is safe for concurrent use across tasks.
    """

    def __init__(self) -> None:
        provider: str = config.get("objectstore.provider", "s3")
        self._endpoint: str = config.get("objectstore.endpoint", "")
        self._bucket: str = config.get("objectstore.bucket", "")
        self._region: str = config.get("objectstore.region", "")
        self._access_key: str = config.get("objectstore.access_key", "")
        self._access_secret: str = config.get("objectstore.access_secret", "")
        self._session: aioboto3.Session = aioboto3.Session()

        # OSS does not support aws-chunked transfer encoding, and only accepts
        # virtual-hosted style addressing (bucket as subdomain). With a custom
        # endpoint_url botocore defaults to path style, which OSS rejects with
        # SecondLevelDomainForbidden.
        if provider == "oss":
            self._boto_config = BotoConfig(
                s3={
                    "addressing_style": "virtual",
                    "payload_signing_enabled": True,
                },
                request_checksum_calculation="when_required",
            )
        else:
            self._boto_config = BotoConfig()

        logger.info(
            "ObjectStoreClient initialised (provider={}, endpoint={}, bucket={})",
            provider,
            self._endpoint,
            self._bucket,
        )

    # ------------------------------------------------------------------
    # Internal helper
    # ------------------------------------------------------------------

    def _client_ctx(self) -> aioboto3.Session.client:
        """Return a fresh async S3 client context manager."""
        return self._session.client(
            "s3",
            endpoint_url=self._endpoint or None,
            region_name=self._region or None,
            aws_access_key_id=self._access_key or None,
            aws_secret_access_key=self._access_secret or None,
            config=self._boto_config,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def upload_file(
        self,
        key: str,
        data: bytes,
        content_type: str | None = None,
    ) -> None:
        """Upload *data* to *key* in the configured bucket."""
        extra: dict[str, str] = {}
        if content_type:
            extra["ContentType"] = content_type

        async with self._client_ctx() as s3:
            await s3.put_object(Bucket=self._bucket, Key=key, Body=data, **extra)

        logger.debug("Uploaded {} ({} bytes)", key, len(data))

    async def delete_file(self, key: str) -> None:
        """Delete an object. No-op when key already absent."""
        async with self._client_ctx() as s3:
            await s3.delete_object(Bucket=self._bucket, Key=key)
        logger.debug("Deleted {}", key)

    async def download_file(self, key: str) -> tuple[bytes, str]:
        """Download an object and return ``(data, content_type)``."""
        async with self._client_ctx() as s3:
            resp = await s3.get_object(Bucket=self._bucket, Key=key)
            data: bytes = await resp["Body"].read()
            content_type: str = resp.get("ContentType", "application/octet-stream")

        logger.debug("Downloaded {} ({} bytes)", key, len(data))
        return data, content_type

    async def list_objects(self, prefix: str) -> list[str]:
        """List all object keys under *prefix*."""
        keys: list[str] = []
        async with self._client_ctx() as s3:
            paginator = s3.get_paginator("list_objects_v2")
            async for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    keys.append(obj["Key"])
        return keys

    async def upload_from_sandbox(
        self,
        sandbox: Sandbox,
        sandbox_path: str,
        key_prefix: str,
    ) -> list[str]:
        """Upload a file or directory from *sandbox* to object storage.

        For directories the method uses ``find`` inside the sandbox to
        enumerate files and then uploads each one.  Returns the list of
        uploaded object keys.
        """
        # Determine whether the path is a file or directory.
        result = await sandbox.execute(f'test -d "{sandbox_path}" && echo DIR || echo FILE')
        is_dir = result.output.strip() == "DIR"

        if is_dir:
            find_result = await sandbox.execute(f'find "{sandbox_path}" -type f')
            if find_result.exit_code and find_result.exit_code != 0:
                logger.warning(
                    "find failed in sandbox (exit={}): {}",
                    find_result.exit_code,
                    find_result.output,
                )
                return []

            file_paths = [p for p in find_result.output.strip().splitlines() if p]
        else:
            file_paths = [sandbox_path]

        if not file_paths:
            return []

        # Download all files in a single call.
        downloaded = await sandbox.download(file_paths)

        uploaded_keys: list[str] = []
        for path, data in downloaded:
            # Build the object key by appending the relative portion.
            if is_dir:
                rel = posixpath.relpath(path, sandbox_path)
            else:
                rel = posixpath.basename(path)
            key = posixpath.join(key_prefix, rel) if key_prefix else rel
            # Normalise away any leading slashes or double slashes.
            key = key.lstrip("/")

            await self.upload_file(key, data)
            uploaded_keys.append(key)

        logger.info(
            "Uploaded {} file(s) from sandbox:{} -> {}",
            len(uploaded_keys),
            sandbox_path,
            key_prefix,
        )
        return uploaded_keys


# ------------------------------------------------------------------
# Singleton
# ------------------------------------------------------------------

_client: ObjectStoreClient | None = None


def get_objectstore_client() -> ObjectStoreClient:
    """Return (and lazily create) the global ``ObjectStoreClient``."""
    global _client
    if _client is None:
        _client = ObjectStoreClient()
    return _client
