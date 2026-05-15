"""cubebox CLI.

Subcommands:

- ``cubebox admin grant-admin / revoke-admin`` — operator user-role mgmt
- ``cubebox seed-mcp-templates`` — idempotently upsert the v1 MCP
  connector templates into the database; reads
  ``CUBEBOX_MCP_OAUTH__<SLUG>__CLIENT_ID/SECRET`` for connectors that
  don't support DCR.

Both invocations work:

    cubebox seed-mcp-templates --dry-run   # via project.scripts entry
    python -m cubebox.cli seed-mcp-templates --dry-run   # via __main__
"""

import click

from cubebox.cli.admin import admin_group
from cubebox.cli.seed_mcp_templates import seed_mcp_templates


@click.group()
def main() -> None:
    """cubebox operator CLI."""


main.add_command(admin_group)
main.add_command(seed_mcp_templates)
