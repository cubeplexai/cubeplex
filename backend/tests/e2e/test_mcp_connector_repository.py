"""Repository invariants for MCP connector identities."""

from __future__ import annotations

import secrets

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.mcp._constants import server_url_hash, slugify_for_namespace
from cubebox.models import MCPConnector, MCPConnectorTemplate, Organization
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

    # template_id is NOT NULL (FK); create a minimal template first.
    template = MCPConnectorTemplate(
        slug=f"repo-test-{suffix}",
        name=f"Repo Test Template {suffix}",
        description="test",
        provider="test",
        server_url=url,
        transport="streamable_http",
        supported_auth_methods=["oauth"],
        default_credential_policy="org",
        scope="global",
    )
    db_session.add(template)
    await db_session.flush()

    connector = MCPConnector(
        org_id=org.id,
        template_id=template.id,
        name="Example MCP",
        server_url=url,
        server_url_hash=server_url_hash(url),
        transport="streamable_http",
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
