"""cubebox CLI."""

import click

from cubebox.cli.admin import admin_group


@click.group()
def main() -> None:
    """cubebox operator CLI."""


main.add_command(admin_group)
