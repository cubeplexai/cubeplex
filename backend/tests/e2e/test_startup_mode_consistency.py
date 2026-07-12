"""Lifespan refuses to start single_tenant when DB has > 1 org."""

import secrets

import pytest

from cubeplex.api.app import create_app, lifespan
from cubeplex.repositories import OrganizationRepository

pytestmark = pytest.mark.e2e


async def test_startup_aborts_on_multi_org_in_single_tenant(session_factory):
    """Startup check aborts in single_tenant mode when DB has >1 orgs."""
    # Seed 2 orgs with random unique slugs so the test is idempotent across
    # runs.
    async with session_factory() as session:
        await OrganizationRepository(session).create(name="o1", slug=f"o1-{secrets.token_hex(3)}")
        await OrganizationRepository(session).create(name="o2", slug=f"o2-{secrets.token_hex(3)}")

    app = create_app()
    app.state.deployment_mode = "single_tenant"
    with pytest.raises(RuntimeError, match="single_tenant requires"):
        async with lifespan(app):
            pass
