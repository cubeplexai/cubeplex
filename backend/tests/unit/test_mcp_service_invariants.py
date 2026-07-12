"""Service-layer invariants for ``MCPConnectorService``.

These tests exercise the pure service logic with fake repositories so the
invariants documented inside ``cubebox/services/mcp_installs.py`` are
guarded without a real DB session. The focus is the
``auto_enroll_new_workspaces`` derivation from ``distribution.mode`` at
connector create time — a wrong default here causes the
``workspace_bootstrap.enroll_workspace_in_org_wide_mcp`` hook to silently
fan an explicitly-scoped connector out into every newly-created workspace.
"""

from __future__ import annotations

from typing import Any

import pytest

from cubebox.mcp.workspace_bootstrap import enroll_workspace_in_org_wide_mcp
from cubebox.models.mcp import MCPConnector
from cubebox.services.mcp_installs import MCPConnectorService

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _NoConflictSession:
    """Stub session whose ``execute()`` always reports no conflict.

    ``MCPConnectorService`` may call through repository-backed preflights; for
    these auto-enroll invariant tests we just need any such preflight to find
    nothing and let the fake connector repository capture the connector row.
    """

    async def execute(self, *_args: Any, **_kwargs: Any) -> Any:
        class _EmptyResult:
            def scalars(self) -> _EmptyResult:
                return self

            def first(self) -> None:
                return None

            def scalar_one_or_none(self) -> None:
                return None

        return _EmptyResult()

    def add(self, _obj: Any) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def refresh(self, _obj: Any) -> None:
        return None


class _FakeConnectorRepo:
    def __init__(self, existing: MCPConnector | None = None) -> None:
        self.added: list[MCPConnector] = []
        self.updated: list[MCPConnector] = []
        self._existing = existing
        self.session = _NoConflictSession()

    async def get(self, connector_id: str) -> MCPConnector | None:
        if self._existing is not None and self._existing.id == connector_id:
            return self._existing
        return None

    async def get_active_by_identity(self, **_kwargs: Any) -> MCPConnector | None:
        return self._existing

    async def add(self, connector: MCPConnector) -> MCPConnector:
        self.added.append(connector)
        return connector

    async def update(self, connector: MCPConnector) -> MCPConnector:
        self.updated.append(connector)
        return connector


class _FakeGrantRepo:
    def __init__(self) -> None:
        self._grants: dict[tuple[str, str, str | None, str | None], Any] = {}

    async def get_for_connector_scope(
        self,
        *,
        connector_id: str,
        grant_scope: str,
        workspace_id: str | None,
        user_id: str | None,
    ) -> Any | None:
        return self._grants.get((connector_id, grant_scope, workspace_id, user_id))

    async def add(self, grant: Any) -> Any:
        key = (grant.connector_id, grant.grant_scope, grant.workspace_id, grant.user_id)
        self._grants[key] = grant
        return grant

    async def update(self, grant: Any) -> Any:
        key = (grant.connector_id, grant.grant_scope, grant.workspace_id, grant.user_id)
        self._grants[key] = grant
        return grant


class _FakeCredentialService:
    def __init__(self) -> None:
        self.upserts: list[dict[str, str]] = []

    async def upsert_by_kind_name(self, *, kind: str, name: str, plaintext: str) -> str:
        self.upserts.append({"kind": kind, "name": name, "plaintext": plaintext})
        return f"cred-{len(self.upserts)}"


class _FakeStateRepo:
    def __init__(self) -> None:
        self.upserts: list[dict[str, Any]] = []

    async def upsert_for_connector(self, **kwargs: Any) -> None:
        self.upserts.append(kwargs)


def _make_static_connector(**overrides: Any) -> MCPConnector:
    values: dict[str, Any] = {
        "id": "mcpco-static",
        "org_id": "org-1",
        "template_id": "mctpl-static",
        "name": "Static Test Template",
        "server_url": "https://static.example.com/mcp",
        "server_url_hash": "hash-static",
        "transport": "streamable_http",
        "auth_method": "static",
        "default_credential_policy": "workspace",
        "auth_status": "pending",
        "created_by_user_id": "usr-1",
    }
    values.update(overrides)
    return MCPConnector(**values)


# ---------------------------------------------------------------------------
# static grant credential names include scope identity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_static_workspace_grants_default_to_distinct_credential_names() -> None:
    connector = _make_static_connector()
    cred_service = _FakeCredentialService()
    svc = MCPConnectorService(
        state_repo=object(),  # type: ignore[arg-type]
        grant_repo=_FakeGrantRepo(),  # type: ignore[arg-type]
        cred_service=cred_service,  # type: ignore[arg-type]
        org_id="org-1",
        actor_user_id="usr-1",
        connector_repo=_FakeConnectorRepo(existing=connector),
    )

    await svc.create_static_grant(
        connector_id=connector.id,
        grant_scope="workspace",
        workspace_id="ws-a",
        plaintext="token-a",
    )
    await svc.create_static_grant(
        connector_id=connector.id,
        grant_scope="workspace",
        workspace_id="ws-b",
        plaintext="token-b",
    )

    assert [row["name"] for row in cred_service.upserts] == [
        f"mcp:{connector.id}:workspace:ws-a",
        f"mcp:{connector.id}:workspace:ws-b",
    ]


@pytest.mark.asyncio
async def test_static_user_grants_default_to_distinct_credential_names() -> None:
    connector = _make_static_connector()
    cred_service = _FakeCredentialService()
    svc = MCPConnectorService(
        state_repo=object(),  # type: ignore[arg-type]
        grant_repo=_FakeGrantRepo(),  # type: ignore[arg-type]
        cred_service=cred_service,  # type: ignore[arg-type]
        org_id="org-1",
        actor_user_id="usr-1",
        connector_repo=_FakeConnectorRepo(existing=connector),
    )

    await svc.create_static_grant(
        connector_id=connector.id,
        grant_scope="user",
        workspace_id="ws-a",
        user_id="usr-a",
        plaintext="token-a",
    )
    await svc.create_static_grant(
        connector_id=connector.id,
        grant_scope="user",
        workspace_id="ws-a",
        user_id="usr-b",
        plaintext="token-b",
    )

    assert [row["name"] for row in cred_service.upserts] == [
        f"mcp:{connector.id}:user:ws-a:usr-a",
        f"mcp:{connector.id}:user:ws-a:usr-b",
    ]


# ---------------------------------------------------------------------------
# workspace_bootstrap hook: skips connectors with auto_enroll_new_workspaces=False
# ---------------------------------------------------------------------------


class _FakeScalarResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _FakeScalarResult:
        return self

    def all(self) -> list[Any]:
        return list(self._rows)


@pytest.mark.asyncio
async def test_bootstrap_hook_skips_connector_with_auto_enroll_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``enroll_workspace_in_org_wide_mcp`` must skip connectors flagged False.

    Belt-and-suspenders with the create-time test above: even if some
    future refactor regresses the create-side default, the bootstrap hook
    itself must not enroll a connector whose flag was explicitly turned off.
    """
    connector_off = MCPConnector(
        id="mcpco-off",
        org_id="org-1",
        template_id="mctpl-x",
        name="off-connector",
        server_url="https://a.example.com/mcp",
        server_url_hash="hash-a",
        transport="streamable_http",
        auth_method="static",
        default_credential_policy="org",
        auth_status="pending",
        auto_enroll_new_workspaces=False,
        created_by_user_id="usr-1",
    )
    connector_on = MCPConnector(
        id="mcpco-on",
        org_id="org-1",
        template_id="mctpl-y",
        name="on-connector",
        server_url="https://b.example.com/mcp",
        server_url_hash="hash-b",
        transport="streamable_http",
        auth_method="static",
        default_credential_policy="org",
        auth_status="pending",
        auto_enroll_new_workspaces=True,
        created_by_user_id="usr-1",
    )

    upserts: list[dict[str, Any]] = []

    class _FakeStateRepoBootstrap:
        def __init__(self, *args: Any, **kwargs: Any) -> None:  # noqa: ARG002
            pass

        async def upsert_for_connector(self, **kwargs: Any) -> None:
            upserts.append(kwargs)

    class _FakeConnectorRepoBootstrap:
        def __init__(self, *args: Any, **kwargs: Any) -> None:  # noqa: ARG002
            pass

        async def list_auto_enroll_active(self) -> list[MCPConnector]:
            return [
                connector
                for connector in [connector_off, connector_on]
                if connector.status == "active" and connector.auto_enroll_new_workspaces is True
            ]

    class _FakeTemplateSettingsRepo:
        def __init__(self, *args: Any, **kwargs: Any) -> None:  # noqa: ARG002
            pass

        async def disabled_template_ids(self) -> set[str]:
            return set()

    monkeypatch.setattr(
        "cubebox.mcp.workspace_bootstrap.MCPWorkspaceConnectorStateRepository",
        _FakeStateRepoBootstrap,
    )
    monkeypatch.setattr(
        "cubebox.mcp.workspace_bootstrap.MCPConnectorRepository",
        _FakeConnectorRepoBootstrap,
    )
    monkeypatch.setattr(
        "cubebox.mcp.workspace_bootstrap.MCPTemplateSettingsRepository",
        _FakeTemplateSettingsRepo,
    )

    session = object()
    await enroll_workspace_in_org_wide_mcp(
        session,  # type: ignore[arg-type]
        org_id="org-1",
        workspace_id="ws-new",
        actor_user_id="usr-1",
    )

    # Only the connector with auto_enroll_new_workspaces=True should have
    # produced a state-row upsert for the new workspace.
    assert len(upserts) == 1, upserts
    assert upserts[0]["connector_id"] == connector_on.id
    assert upserts[0]["workspace_id"] == "ws-new"
