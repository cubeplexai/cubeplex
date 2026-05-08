"""cubebox CLI.

Subcommands:

- ``cubebox admin grant-admin / revoke-admin`` — operator user-role mgmt
- ``cubebox seed-mcp-catalog`` — idempotently upsert the v1 MCP catalog
  into the database; reads ``CUBEBOX_MCP_OAUTH__<SLUG>__CLIENT_ID/SECRET``
  for connectors that don't support DCR

Both invocations work:

    cubebox seed-mcp-catalog --dry-run   # via project.scripts entry
    python -m cubebox.cli seed-mcp-catalog --dry-run   # via __main__
"""

import click

from cubebox.cli.admin import admin_group
from cubebox.cli.seed_mcp_catalog import seed_mcp_catalog


@click.group()
def main() -> None:
    """cubebox operator CLI."""


main.add_command(admin_group)
main.add_command(seed_mcp_catalog)
