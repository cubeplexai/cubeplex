"""Single deployment entry point for schema upgrades and lifecycle cutover."""

from __future__ import annotations

import argparse
import asyncio
from enum import StrEnum
from pathlib import Path
from typing import Any

import psycopg
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.script.revision import RangeNotAncestorError

from alembic import command as alembic_command
from cubeplex.config import backend_dir, config

EXPAND_HEAD_REVISION = "76a2d219d682"
CUTOVER_REVISION = "034d0feb3d1e"
MIGRATION_LOCK_ID = 0x435042475441534B


class UpgradeAction(StrEnum):
    install = "install"
    upgrade = "upgrade"
    maintenance = "maintenance"
    refuse = "refuse"


def decide_upgrade(
    *,
    fresh: bool,
    cutover_complete: bool,
    maintenance: bool,
) -> UpgradeAction:
    if fresh:
        return UpgradeAction.install
    if maintenance:
        return UpgradeAction.maintenance
    if cutover_complete:
        return UpgradeAction.upgrade
    return UpgradeAction.refuse


def _alembic_config() -> Config:
    settings = Config(str(Path(backend_dir) / "alembic.ini"))
    settings.set_main_option("script_location", str(Path(backend_dir) / "alembic"))
    return settings


def _connect() -> psycopg.Connection[Any]:
    return psycopg.connect(
        host=str(config.get("database.host", "localhost")),
        port=int(config.get("database.port", 5432)),
        user=str(config.get("database.user", "postgres")),
        password=str(config.get("database.password", "")),
        dbname=str(config.get("database.name", "cubeplex")),
        autocommit=True,
    )


def _database_revision(connection: psycopg.Connection[Any]) -> tuple[bool, str | None]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT to_regclass('public.alembic_version'), to_regclass('public.organizations')"
        )
        alembic_table, organizations_table = cursor.fetchone() or (None, None)
        if alembic_table is None:
            if organizations_table is not None:
                raise RuntimeError(
                    "database has CubePlex tables but no alembic_version; refusing to guess"
                )
            return True, None
        cursor.execute("SELECT version_num FROM alembic_version")
        revisions = [str(row[0]) for row in cursor.fetchall()]
    if len(revisions) != 1:
        raise RuntimeError(f"expected one Alembic head, found {len(revisions)}")
    return False, revisions[0]


def _contains_cutover(script: ScriptDirectory, revision: str) -> bool:
    if revision == CUTOVER_REVISION:
        return True
    try:
        return any(
            item.revision == revision
            for item in script.iterate_revisions(revision, CUTOVER_REVISION)
        )
    except RangeNotAncestorError:
        return False


async def _backfill() -> None:
    from cubeplex.db.engine import async_session_maker, engine
    from cubeplex.scripts.dev.migrate_background_tasks import (
        load_checkpointed_notice_ids,
        migrate_legacy_commands,
    )

    try:
        async with async_session_maker() as session:
            checkpointed_notice_ids = await load_checkpointed_notice_ids(session)
            report = await migrate_legacy_commands(
                session,
                apply=True,
                checkpointed_notice_ids=checkpointed_notice_ids,
            )
            if report.blockers:
                await session.rollback()
                details = ", ".join(f"{item.command_id}: {item.reason}" for item in report.blockers)
                raise RuntimeError(f"lifecycle backfill blocked: {details}")
            await session.commit()
            print(
                "background-task backfill committed: "
                f"{report.migrated} command(s), {report.events_migrated} event(s)"
            )
    finally:
        await engine.dispose()


async def _verify() -> None:
    from cubeplex.db.engine import async_session_maker, engine
    from cubeplex.services.background_task_cutover import require_background_task_cutover

    try:
        async with async_session_maker() as session:
            await require_background_task_cutover(session)
    finally:
        await engine.dispose()


def _upgrade(target: str) -> None:
    alembic_command.upgrade(_alembic_config(), target)


def run_upgrade(*, maintenance: bool) -> int:
    settings = _alembic_config()
    script = ScriptDirectory.from_config(settings)
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", (MIGRATION_LOCK_ID,))
            acquired = bool((cursor.fetchone() or (False,))[0])
        if not acquired:
            print("another CubePlex schema upgrade owns the database lock")
            return 2
        try:
            fresh, current = _database_revision(connection)
            cutover_complete = bool(current and _contains_cutover(script, current))
            action = decide_upgrade(
                fresh=fresh,
                cutover_complete=cutover_complete,
                maintenance=maintenance,
            )
            if action == UpgradeAction.refuse:
                print(
                    "existing database requires lifecycle maintenance: stop every old "
                    "API/worker/coordinator, then run `python -m "
                    "cubeplex.scripts.lifecycle_upgrade --maintenance` once"
                )
                return 2
            if action == UpgradeAction.maintenance:
                if not cutover_complete:
                    _upgrade(EXPAND_HEAD_REVISION)
                asyncio.run(_backfill())
            _upgrade("head")
            asyncio.run(_verify())
            print(f"database upgrade complete ({action.value})")
            return 0
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_ID,))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Upgrade CubePlex without bypassing the lifecycle cutover gate."
    )
    parser.add_argument(
        "--maintenance",
        action="store_true",
        help="assert that all old writers are stopped and run the one-time backfill",
    )
    args = parser.parse_args()
    return run_upgrade(maintenance=args.maintenance)


if __name__ == "__main__":
    raise SystemExit(main())
