"""Repository invariants for MCP connector identities."""

from __future__ import annotations

import secrets

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.mcp._constants import server_url_hash, slugify_for_namespace
from cubebox.models import MCPConnector, Organization
from cubebox.repositories.mcp import MCPConnectorRepository

pytestmark = pytest.mark.e2e


async def test_get_active_by_identity_matches_template_url_or_slug(
    db_session: AsyncSession,
) -> None:
    suffix = secrets.token_hex(4)
    org = Organization(name=f"Connector Repo Org {suffix}", slug=f"connector-repo-org-{suffix}")
    db_session.add(org)
    await db_session.flush()

    url = "https://mcp.example.com"
    connector = MCPConnector(
        org_id=org.id,
        template_id=None,
        name="Example MCP",
        server_url=url,
        server_url_hash=server_url_hash(url),
        transport="streamable_http",
        auth_method="oauth",
        status="active",
    )
    db_session.add(connector)
    await db_session.commit()

    repo = MCPConnectorRepository(db_session, org_id=org.id)

    found_by_url = await repo.get_active_by_identity(
        template_id=None,
        server_url_hash=server_url_hash(url),
        slug_name=slugify_for_namespace("different"),
    )
    found_by_slug = await repo.get_active_by_identity(
        template_id=None,
        server_url_hash=server_url_hash("https://different.example.com"),
        slug_name=slugify_for_namespace("Example MCP"),
    )

    assert found_by_url is not None
    assert found_by_url.id == connector.id
    assert found_by_slug is not None
    assert found_by_slug.id == connector.id
