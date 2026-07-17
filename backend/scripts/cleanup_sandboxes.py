"""Cleanup script for stale/running OpenSandbox instances from the local DB record."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import opensandbox
from opensandbox.config import ConnectionConfig
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from cubeplex.config import config
from cubeplex.db.engine import _build_database_url
from cubeplex.models.user_sandbox import UserSandbox


def _session_factory() -> tuple[async_sessionmaker[AsyncSession], AsyncEngine]:
    """Build a session factory for the running environment."""
    database_url = _build_database_url()
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return session_factory, engine


def _connection_config() -> ConnectionConfig:
    """Build OpenSandbox connection config from Dynaconf settings."""
    return ConnectionConfig(
        domain=config.get("sandbox.domain", "localhost:8090"),
        api_key=config.get("sandbox.api_key", None),
        request_timeout=timedelta(seconds=config.get("sandbox.request_timeout", 60)),
        use_server_proxy=config.get("sandbox.use_server_proxy", False),
    )


async def main() -> None:
    """Terminate all records in user_sandboxes with status 'running'."""
    session_factory, engine = _session_factory()
    conn_config = _connection_config()

    try:
        async with session_factory() as session:
            query = select(UserSandbox).where(UserSandbox.status == "running")
            rows = (await session.execute(query)).scalars().all()
            if not rows:
                print("No running sandboxes to clean.")
                return

            print(f"Found {len(rows)} running sandbox record(s). Cleaning...")

            for row in rows:
                sandbox_id = row.sandbox_id
                print(f"- cleanup sandbox_id={sandbox_id}")
                try:
                    raw_sandbox = await opensandbox.Sandbox.connect(
                        sandbox_id,
                        connection_config=conn_config,
                        skip_health_check=True,
                    )
                    await raw_sandbox.kill()
                    await raw_sandbox.close()
                except Exception as exc:  # noqa: BLE001
                    print(f"  warning: failed to kill {sandbox_id}: {exc}")

                row.status = "terminated"

            await session.commit()
            print("Cleanup done.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
