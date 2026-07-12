"""``cubeplex seed-mcp-templates`` — idempotently upsert the v1 MCP templates.

Reads the static template list in ``cubeplex.mcp.template_seed``, ingests
static OAuth client_id / client_secret pairs from environment variables
for connectors that don't support DCR
(``CUBEPLEX_MCP_OAUTH__<SLUG>__CLIENT_ID`` and
``CUBEPLEX_MCP_OAUTH__<SLUG>__CLIENT_SECRET``), encrypts the secrets into
system-level credential rows, and upserts each connector into
``mcp_connector_templates``. Slugs absent from the in-process template
list are marked ``status='deprecated'`` (rows are kept so installs
remain referencable).

``--dry-run`` runs the seeder inside a transaction that's rolled back
at the end, then prints the would-be summary. Useful for verifying env
var coverage on a host before committing changes.
"""

from __future__ import annotations

import asyncio
import os
import sys

import click
from loguru import logger

# Bootstrapping order matters: dynaconf settings must load before any
# module that touches ``cubeplex.config``. Importing the engine here is
# enough to trigger config init.
from cubeplex.config import config  # noqa: F401  (side effects)


@click.command(name="seed-mcp-templates")
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Roll back instead of committing (still prints the would-be summary).",
)
@click.option(
    "--quiet",
    is_flag=True,
    default=False,
    help="Reduce log output (warnings + summary only).",
)
def seed_mcp_templates(dry_run: bool, quiet: bool) -> None:
    """Idempotently upsert the v1 MCP connector templates into the database."""
    exit_code = asyncio.run(_run(dry_run=dry_run, quiet=quiet))
    sys.exit(exit_code)


async def _run(*, dry_run: bool, quiet: bool) -> int:
    # Local imports keep CLI startup snappy and avoid pulling FastAPI
    # into the import graph for a one-shot DB script.
    from cubeplex.api.app import _build_encryption_backend
    from cubeplex.db.engine import async_session_maker
    from cubeplex.mcp.template_seed import seed_templates

    if quiet:
        logger.remove()
        logger.add(sys.stderr, level="WARNING")

    backend = _build_encryption_backend()

    async with async_session_maker() as session:
        try:
            result = await seed_templates(session, backend, get_env=os.getenv)
        except Exception:  # noqa: BLE001 — single failure path
            await session.rollback()
            logger.exception("seed-mcp-templates failed")
            return 1

        if dry_run:
            await session.rollback()
            logger.info("dry-run: rolled back")
        else:
            await session.commit()

    suffix = " (dry run, rolled back)" if dry_run else ""
    click.echo(
        f"seed-mcp-templates: upserted={result.upserted} "
        f"skipped={result.skipped} deprecated={result.deprecated}{suffix}"
    )
    for warning in result.warnings:
        click.echo(f"  warning: {warning}")
    return 0
