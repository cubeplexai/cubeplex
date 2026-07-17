"""cubeplex admin subcommands: grant-admin / revoke-admin / disable-sso / list-sso."""

import asyncio
import sys

import click
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.db import async_session_maker
from cubeplex.models import Organization, OrgRole, User
from cubeplex.models.sso_connection import SSOConnection
from cubeplex.repositories import OrganizationMembershipRepository


@click.group(name="admin")
def admin_group() -> None:
    """Operator-level admin commands."""


async def _resolve_user_and_org(
    session: AsyncSession, email: str, org_slug: str | None
) -> tuple[User, Organization]:
    user = (
        await session.execute(select(User).where(User.email == email))  # type: ignore[arg-type]
    ).scalar_one_or_none()
    if user is None:
        click.echo(f"No user with email {email}", err=True)
        sys.exit(1)

    if org_slug is None:
        orgs = (await session.execute(select(Organization))).scalars().all()
        if len(orgs) == 0:
            click.echo("No organizations exist; run /setup first.", err=True)
            sys.exit(1)
        if len(orgs) > 1:
            click.echo(
                "Multiple orgs exist; pass --org-slug for multi_tenant mode.",
                err=True,
            )
            sys.exit(1)
        return user, orgs[0]

    org = (
        await session.execute(
            select(Organization).where(Organization.slug == org_slug)  # type: ignore[arg-type]
        )
    ).scalar_one_or_none()
    if org is None:
        click.echo(f"No org with slug {org_slug!r}", err=True)
        sys.exit(1)
    return user, org


@admin_group.command("grant-admin")
@click.argument("email")
@click.option("--org-slug", default=None, help="Required in multi_tenant mode.")
def grant_admin(email: str, org_slug: str | None) -> None:
    """Promote EMAIL to admin of the org (no-op if already admin/owner)."""
    asyncio.run(_grant_admin_async(email, org_slug))


async def _grant_admin_async(email: str, org_slug: str | None) -> None:
    async with async_session_maker() as session:
        user, org = await _resolve_user_and_org(session, email, org_slug)
        repo = OrganizationMembershipRepository(session)
        existing = await repo.get_role(user_id=user.id, org_id=org.id)

        if existing is OrgRole.OWNER:
            click.echo(f"{email} is already owner of {org.slug}; refusing.", err=True)
            sys.exit(1)
        if existing is OrgRole.ADMIN:
            click.echo(f"{email} is already admin of {org.slug}.")
            return

        if existing is None:
            await repo.grant(user_id=user.id, org_id=org.id, role=OrgRole.ADMIN)
        else:
            await repo.promote(user_id=user.id, org_id=org.id, role=OrgRole.ADMIN)
        click.echo(f"Promoted {email} to admin of org {org.slug!r} ({org.id}).")


@admin_group.command("revoke-admin")
@click.argument("email")
@click.option("--org-slug", default=None)
def revoke_admin(email: str, org_slug: str | None) -> None:
    """Demote EMAIL from admin to member; refuses to touch owner."""
    asyncio.run(_revoke_admin_async(email, org_slug))


async def _revoke_admin_async(email: str, org_slug: str | None) -> None:
    async with async_session_maker() as session:
        user, org = await _resolve_user_and_org(session, email, org_slug)
        repo = OrganizationMembershipRepository(session)
        existing = await repo.get_role(user_id=user.id, org_id=org.id)
        if existing is OrgRole.OWNER:
            click.echo(f"{email} is owner of {org.slug}; cannot revoke owner.", err=True)
            sys.exit(1)
        if existing is None or existing is OrgRole.MEMBER:
            click.echo(f"{email} is already not an admin of {org.slug}.")
            return
        await repo.promote(user_id=user.id, org_id=org.id, role=OrgRole.MEMBER)
        click.echo(f"Demoted {email} to member of org {org.slug!r} ({org.id}).")


@admin_group.command("disable-sso")
@click.option("--org-slug", required=True, help="Org slug to disable SSO for.")
def disable_sso(org_slug: str) -> None:
    """Emergency lockout recovery: flip the org's SSO connection to inactive.

    Does not delete the row or clear the credential — operator needs to be
    able to inspect and re-activate after the underlying issue is fixed.
    """
    asyncio.run(_disable_sso_async(org_slug))


async def _disable_sso_async(org_slug: str) -> None:
    async with async_session_maker() as session:
        org = (
            await session.execute(
                select(Organization).where(Organization.slug == org_slug)  # type: ignore[arg-type]
            )
        ).scalar_one_or_none()
        if org is None:
            click.echo(f"No org with slug {org_slug!r}", err=True)
            sys.exit(1)

        conn = (
            await session.execute(
                select(SSOConnection).where(
                    SSOConnection.org_id == org.id  # type: ignore[arg-type]
                )
            )
        ).scalar_one_or_none()
        if conn is None:
            click.echo(f"No SSO connection for org {org_slug!r}.", err=True)
            sys.exit(1)

        previous_status = conn.status
        conn.status = "inactive"
        session.add(conn)
        await session.commit()
        click.echo(
            f"Disabled SSO for org {org_slug!r} (sso_id={conn.id}, was {previous_status!r})."
        )


@admin_group.command("list-sso")
def list_sso() -> None:
    """List all SSO connections (org_slug | protocol | status | provisioning | display_name | sso_id)."""
    asyncio.run(_list_sso_async())


async def _list_sso_async() -> None:
    async with async_session_maker() as session:
        rows = (
            await session.execute(
                select(SSOConnection, Organization).join(
                    Organization,
                    SSOConnection.org_id == Organization.id,  # type: ignore[arg-type]
                )
            )
        ).all()
        if not rows:
            click.echo("No SSO connections configured.")
            return

        header = (
            f"{'org_slug':<20}  {'protocol':<8}  {'status':<10}  "
            f"{'provisioning':<14}  {'display_name':<30}  sso_id"
        )
        click.echo(header)
        click.echo("-" * len(header))
        for conn, org in rows:
            click.echo(
                f"{org.slug:<20}  {conn.protocol:<8}  {conn.status:<10}  "
                f"{conn.provisioning:<14}  {conn.display_name:<30}  {conn.id}"
            )
