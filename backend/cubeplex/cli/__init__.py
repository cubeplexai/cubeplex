"""cubeplex CLI.

Subcommands:

- ``cubeplex admin grant-admin / revoke-admin`` — operator user-role mgmt
- ``cubeplex seed-mcp-templates`` — idempotently upsert the v1 MCP
  connector templates into the database; reads
  ``CUBEPLEX_MCP_OAUTH__<SLUG>__CLIENT_ID/SECRET`` for connectors that
  don't support DCR.

Both invocations work:

    cubeplex seed-mcp-templates --dry-run   # via project.scripts entry
    python -m cubeplex.cli seed-mcp-templates --dry-run   # via __main__
"""

import click

from cubeplex.cli.admin import admin_group
from cubeplex.cli.seed_mcp_templates import seed_mcp_templates


@click.group()
def main() -> None:
    """cubeplex operator CLI."""


main.add_command(admin_group)
main.add_command(seed_mcp_templates)
