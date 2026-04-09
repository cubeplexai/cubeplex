"""Object storage client for S3/OSS backends."""

from cubebox.objectstore.client import ObjectStoreClient, get_objectstore_client

__all__ = ["ObjectStoreClient", "get_objectstore_client"]
