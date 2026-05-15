"""cubebox CLI.

Subcommands:

- ``cubebox admin grant-admin / revoke-admin`` — operator user-role mgmt
- ``cubebox seed-mcp-templates`` — idempotently upsert the v1 MCP
  connector templates into the database; reads
  ``CUBEBOX_MCP_OAUTH__<SLUG>__CLIENT_ID/SECRET`` for connectors that
  don't support DCR.
- ``cubebox seed-mcp-catalog`` — deprecated alias of
  ``seed-mcp-templates``; removed in plan Task 9.

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

# Back-compat: re-register the same command under its old name so
# ``cubebox seed-mcp-catalog`` keeps working until plan Task 9. We
# create a hidden Click command alias rather than re-using the
# function (which would replace the canonical name on the group).
_legacy_seed_alias = click.Command(
    name="seed-mcp-catalog",
    params=list(seed_mcp_templates.params),
    callback=seed_mcp_templates.callback,
    help="Deprecated alias of seed-mcp-templates (removed in plan Task 9).",
    hidden=True,
)
main.add_command(_legacy_seed_alias)
