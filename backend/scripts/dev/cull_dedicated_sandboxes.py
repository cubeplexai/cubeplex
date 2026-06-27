"""One-shot dev script: kill dedicated topic/group-chat sandbox containers.

Phase 0 of the sandbox-entity migration.  Before Phase 1 soft-deletes active
dedicated UserSandbox rows, this script connects to each live provider
container and kills it, so the migration's sandbox_id=NULL doesn't orphan
anything.

Usage:
    cd backend && uv run python scripts/dev/cull_dedicated_sandboxes.py

Idempotent: skips rows already terminated / missing at the provider.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from cubebox.config import Settings
from cubebox.sandbox.models import UserSandbox
from cubebox.sandbox.provider import SandboxProvider

logger = logging.getLogger("cull_dedicated_sandboxes")

# Scope-type string values used for dedicated sandboxes in the codebase.
SCOPE_TYPE_TOPIC = "topic"
SCOPE_TYPE_GROUP_CHAT = "conversation"

# Sandbox status values.
STATUS_ACTIVE = "active"


def _load_settings() -> Settings:
    """Load application settings from environment / config files."""
    return Settings()  # type: ignore[call-arg]


async def cull_dedicated_sandboxes(dry_run: bool = False) -> int:
    """Kill all active dedicated sandbox containers.

    Returns the number of sandbox records processed.
    """
    settings = _load_settings()

    # Build DB engine and session.
    engine = create_async_engine(str(settings.database.url), echo=False)
    session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine,
        expire_on_commit=False,
    )

    # Build the sandbox provider.
    provider = SandboxProvider(settings)

    async with session_factory() as session:
        # Query active dedicated UserSandbox rows that have a live sandbox_id.
        stmt = select(UserSandbox).where(
            UserSandbox.scope_type.in_([SCOPE_TYPE_TOPIC, SCOPE_TYPE_GROUP_CHAT]),
            UserSandbox.status == STATUS_ACTIVE,
            UserSandbox.sandbox_id.isnot(None),
        )
        result = await session.execute(stmt)
        records = list(result.scalars().all())

        if not records:
            logger.info("No active dedicated sandbox records found.")
            return 0

        logger.info("Found %d active dedicated sandbox record(s).", len(records))

        for record in records:
            sandbox_id = record.sandbox_id
            logger.info(
                "Processing record %s (user=%s, scope_type=%s, scope_id=%s, sandbox_id=%s)",
                record.id,
                record.user_id,
                record.scope_type,
                record.scope_id,
                sandbox_id,
            )

            if dry_run:
                logger.info("[DRY-RUN] Would kill sandbox %s", sandbox_id)
                continue

            try:
                # Kill the provider-side container.
                await provider.destroy(sandbox_id)
                logger.info("Killed provider container %s", sandbox_id)
            except Exception:
                logger.exception(
                    "Failed to kill provider container %s (may already be gone)",
                    sandbox_id,
                )

            # Mark the DB record as terminated.
            record.status = "terminated"
            record.terminated_at = datetime.now(UTC)
            session.add(record)

        await session.commit()
        logger.info("Committed %d record(s).", len(records))

    await engine.dispose()
    return len(records)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )

    dry_run = "--dry-run" in sys.argv
    if dry_run:
        sys.argv.remove("--dry-run")

    count = asyncio.run(cull_dedicated_sandboxes(dry_run=dry_run))
    logger.info("Done. Processed %d record(s).", count)


if __name__ == "__main__":
    main()
