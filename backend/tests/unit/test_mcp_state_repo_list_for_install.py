import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

from cubebox.repositories.mcp import MCPWorkspaceConnectorStateRepository


@pytest.mark.asyncio
async def test_list_for_connector_returns_only_matching_connector(tmp_path):
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    async with AsyncSession(eng) as session:
        repo = MCPWorkspaceConnectorStateRepository(session, org_id="org-1")
        await repo.upsert_for_connector(
            workspace_id="ws-a",
            connector_id="mcpco-x",
            enabled=True,
            credential_policy="org",
            enablement_source="admin_auto",
            updated_by_user_id="usr-1",
        )
        await repo.upsert_for_connector(
            workspace_id="ws-b",
            connector_id="mcpco-x",
            enabled=False,
            credential_policy="org",
            enablement_source="admin_manual",
            updated_by_user_id="usr-1",
        )
        await repo.upsert_for_connector(
            workspace_id="ws-a",
            connector_id="mcpco-other",
            enabled=True,
            credential_policy="org",
            enablement_source="admin_auto",
            updated_by_user_id="usr-1",
        )

        rows = await repo.list_for_connector("mcpco-x")
        assert {r.workspace_id for r in rows} == {"ws-a", "ws-b"}
        assert sum(1 for r in rows if r.enabled) == 1
