"""Copy legacy conversation-prefixed artifact objects to canonical keys.

The migration is idempotent. By default it keeps legacy objects; pass
``--delete-source`` only after every consumer runs the canonical-key code.

Usage:
    cd backend
    uv run python scripts/dev/migrate_artifact_object_keys.py
    uv run python scripts/dev/migrate_artifact_object_keys.py --delete-source
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from loguru import logger
from sqlalchemy import select

from cubeplex.db.engine import async_session_maker
from cubeplex.models.artifact import Artifact
from cubeplex.objectstore import get_objectstore_client
from cubeplex.objectstore.artifact_paths import artifact_root_prefix


async def migrate(*, delete_source: bool) -> tuple[int, int]:
    async with async_session_maker() as session:
        artifacts = list((await session.execute(select(Artifact))).scalars().all())

    store = get_objectstore_client()
    copied = 0
    skipped = 0
    for artifact in artifacts:
        source_prefix = f"artifacts/{artifact.conversation_id}/{artifact.id}/"
        destination_prefix = artifact_root_prefix(artifact.id)
        source_keys = await store.list_objects(source_prefix)
        if not source_keys:
            skipped += 1
            continue

        for source_key in source_keys:
            relative_path = source_key[len(source_prefix) :]
            destination_key = f"{destination_prefix}{relative_path}"
            data, content_type = await store.download_file(source_key)
            await store.upload_file(destination_key, data, content_type)
            copied += 1
            if delete_source:
                await store.delete_file(source_key)

        logger.info(
            "Migrated artifact {}: {} object(s){}",
            artifact.id,
            len(source_keys),
            " and deleted legacy keys" if delete_source else "",
        )

    return copied, skipped


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--delete-source",
        action="store_true",
        help="delete each legacy object after its canonical copy succeeds",
    )
    args = parser.parse_args()
    copied, skipped = await migrate(delete_source=args.delete_source)
    logger.info(
        "Migration complete: {} copied, {} artifacts had no legacy objects",
        copied,
        skipped,
    )


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, format="{time:HH:mm:ss} | {level} | {message}", level="INFO")
    asyncio.run(main())
