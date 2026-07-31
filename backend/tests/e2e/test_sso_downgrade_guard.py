"""E2E: a deployment that loses the optional package but keeps live SSO rows
says so loudly at startup.

Removing the package does not remove its rows — the table is core-owned and
shares the migration lineage. Without this guard the affected org loses *both*
login methods at once: password login is still refused by forced-SSO
enforcement, the login page still advertises SSO because org-info reports it,
and the routes that would serve it are gone.
"""

import importlib.util

import pytest
from sqlalchemy import delete

if importlib.util.find_spec("cubeplex_ee") is not None:
    pytest.skip(
        "the guard only fires when the package is absent",
        allow_module_level=True,
    )

from cubeplex.auth.external_login import report_unserviceable_sso  # noqa: E402
from cubeplex.models import Organization, SSOConnection  # noqa: E402
from tests.e2e.billing_fixtures import _db_session  # noqa: E402

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_active_connection_without_the_package_is_reported() -> None:
    async with _db_session() as session:
        session.add(Organization(id="org-downgrade-1", name="Downgrade 1", slug="downgrade-1"))
        conn = SSOConnection(
            org_id="org-downgrade-1",
            protocol="oidc",
            display_name="Stranded SSO",
            status="active",
            provisioning="auto",
            config={},
        )
        session.add(conn)
        await session.commit()
        try:
            # Asserted about this org only. Other tests write active rows into the
            # same database, so asserting on the whole set — or on the log text,
            # which names just the first few — would pass or fail by run order.
            assert "downgrade-1" in await report_unserviceable_sso(session)
        finally:
            await session.execute(
                delete(SSOConnection).where(
                    SSOConnection.org_id == "org-downgrade-1"  # type: ignore[arg-type]
                )
            )
            await session.execute(
                delete(Organization).where(
                    Organization.id == "org-downgrade-1"  # type: ignore[arg-type]
                )
            )
            await session.commit()


@pytest.mark.asyncio
async def test_inactive_connection_is_not_reported() -> None:
    """`disable-sso` sets status to inactive, so that must be silent — otherwise
    the documented recovery path wouldn't actually recover."""
    async with _db_session() as session:
        session.add(Organization(id="org-downgrade-2", name="Downgrade 2", slug="downgrade-2"))
        conn = SSOConnection(
            org_id="org-downgrade-2",
            protocol="oidc",
            display_name="Disabled SSO",
            status="inactive",
            provisioning="auto",
            config={},
        )
        session.add(conn)
        await session.commit()
        try:
            assert "downgrade-2" not in await report_unserviceable_sso(session)
        finally:
            await session.execute(
                delete(SSOConnection).where(
                    SSOConnection.org_id == "org-downgrade-2"  # type: ignore[arg-type]
                )
            )
            await session.execute(
                delete(Organization).where(
                    Organization.id == "org-downgrade-2"  # type: ignore[arg-type]
                )
            )
            await session.commit()
