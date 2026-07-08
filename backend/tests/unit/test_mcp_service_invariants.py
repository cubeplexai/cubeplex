"""Service-layer invariants for ``MCPConnectorInstallService``.

These tests exercise the pure service logic with fake repositories so the
invariants documented inside ``cubebox/services/mcp_installs.py`` are
guarded without a real DB session. The focus is the
``auto_enroll_new_workspaces`` derivation from ``distribution.mode`` at
install create time — a wrong default here causes the
``workspace_bootstrap.enroll_workspace_in_org_wide_mcp`` hook to silently
fan an explicitly-scoped install out into every newly-created workspace.
"""

from __future__ import annotations

from typing import Any

import pytest

from cubebox.mcp.workspace_bootstrap import enroll_workspace_in_org_wide_mcp
from cubebox.models import (
    MCPConnector,
    MCPConnectorInstall,
    MCPConnectorTemplate,
)
from cubebox.services.mcp_installs import MCPConnectorInstallService

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _NoConflictSession:
    """Stub session whose ``execute()`` always reports no conflict.

    ``MCPConnectorInstallService._has_install_conflict`` runs a SELECT
    through ``install_repo.session.execute(...)``; for these auto-enroll
    invariant tests we just need the preflight to find nothing and let
    the real ``add()`` capture the install row.
    """

    async def execute(self, *_args: Any, **_kwargs: Any) -> Any:
        class _EmptyResult:
            def scalars(self) -> _EmptyResult:
                return self

            def first(self) -> None:
                return None

        return _EmptyResult()

    def add(self, _obj: Any) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def refresh(self, _obj: Any) -> None:
        return None


class _FakeInstallRepo:
    """Capture the row passed to ``add`` so tests can inspect it."""

    def __init__(self) -> None:
        self.added: list[MCPConnectorInstall] = []
        self.session = _NoConflictSession()

    async def add(self, install: MCPConnectorInstall) -> MCPConnectorInstall:
        self.added.append(install)
        return install


class _FakeConnectorRepo:
    def __init__(self) -> None:
        self.added: list[MCPConnector] = []

    async def get_active_by_identity(self, **_kwargs: Any) -> None:
        return None

    async def add(self, connector: MCPConnector) -> MCPConnector:
        self.added.append(connector)
        return connector


class _FakeStateRepo:
    def __init__(self) -> None:
        self.upserts: list[dict[str, Any]] = []

    async def upsert_for_connector(self, **kwargs: Any) -> None:
        self.upserts.append(kwargs)


class _FakeWorkspace:
    def __init__(self, ws_id: str) -> None:
        self.id = ws_id


class _FakeWorkspaceRepo:
    def __init__(self, workspace_ids: list[str]) -> None:
        self._workspaces = [_FakeWorkspace(wid) for wid in workspace_ids]

    async def list_for_org(self, org_id: str) -> list[_FakeWorkspace]:  # noqa: ARG002
        return list(self._workspaces)


def _make_template() -> MCPConnectorTemplate:
    """A minimal template that supports static auth — enough for create."""
    return MCPConnectorTemplate(
        slug="t-static",
        name="Static Test Template",
        description="x",
        provider="acme",
        server_url="https://t.example.com/mcp",
        transport="streamable_http",
        supported_auth_methods=["static", "none"],
        default_credential_policy="org",
    )


def _make_service(
    *,
    workspace_ids: list[str] | None = None,
) -> tuple[MCPConnectorInstallService, _FakeInstallRepo, _FakeStateRepo]:
    install_repo = _FakeInstallRepo()
    connector_repo = _FakeConnectorRepo()
    state_repo = _FakeStateRepo()
    grant_repo = object()  # not used by these paths
    cred_service = object()  # not used by these paths
    workspace_repo = _FakeWorkspaceRepo(workspace_ids) if workspace_ids is not None else None
    svc = MCPConnectorInstallService(
        install_repo=install_repo,  # type: ignore[arg-type]
        state_repo=state_repo,  # type: ignore[arg-type]
        grant_repo=grant_repo,  # type: ignore[arg-type]
        cred_service=cred_service,  # type: ignore[arg-type]
        org_id="org-1",
        actor_user_id="usr-1",
        workspace_repo=workspace_repo,  # type: ignore[arg-type]
        connector_repo=connector_repo,
    )
    return svc, install_repo, state_repo


# ---------------------------------------------------------------------------
# auto_enroll_new_workspaces derivation at install create time
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_org_install_selected_disables_auto_enroll() -> None:
    """``distribution.mode='selected'`` must persist ``auto_enroll_new_workspaces=False``.

    An admin asking for a curated workspace list does NOT want the install
    to silently fan out to workspaces created later. Without this, the
    model's ``server_default=true`` leaks the scope.
    """
    svc, install_repo, _ = _make_service(workspace_ids=["ws-a"])
    saved = await svc.create_from_template_for_org(
        template=_make_template(),
        auth_method="static",
        credential_policy="org",
        distribution={"mode": "selected", "workspace_ids": ["ws-a"]},
    )
    assert saved.install.auto_enroll_new_workspaces is False
    assert install_repo.added[0].auto_enroll_new_workspaces is False


@pytest.mark.asyncio
async def test_create_org_install_none_disables_auto_enroll() -> None:
    """``distribution.mode='none'`` must persist ``auto_enroll_new_workspaces=False``.

    Mode 'none' means "install row only, no state rows yet"; the admin will
    enable workspaces by hand later. Auto-enrolling into newly-created
    workspaces would contradict that intent.
    """
    svc, install_repo, _ = _make_service(workspace_ids=[])
    saved = await svc.create_from_template_for_org(
        template=_make_template(),
        auth_method="static",
        credential_policy="org",
        distribution={"mode": "none"},
    )
    assert saved.install.auto_enroll_new_workspaces is False
    assert install_repo.added[0].auto_enroll_new_workspaces is False


@pytest.mark.asyncio
async def test_create_org_install_all_enables_auto_enroll() -> None:
    """``distribution.mode='all'`` must persist ``auto_enroll_new_workspaces=True``.

    'all' is the only mode where future workspaces should inherit the
    install automatically — the admin explicitly opted into org-wide reach.
    """
    svc, install_repo, _ = _make_service(workspace_ids=["ws-a", "ws-b"])
    saved = await svc.create_from_template_for_org(
        template=_make_template(),
        auth_method="static",
        credential_policy="org",
        distribution={"mode": "all"},
    )
    assert saved.install.auto_enroll_new_workspaces is True
    assert install_repo.added[0].auto_enroll_new_workspaces is True


# ---------------------------------------------------------------------------
# workspace_bootstrap hook: skips installs with auto_enroll_new_workspaces=False
# ---------------------------------------------------------------------------


class _FakeScalarResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _FakeScalarResult:
        return self

    def all(self) -> list[Any]:
        return list(self._rows)


class _FakeSession:
    """Minimal AsyncSession stand-in: ``execute(stmt)`` returns the pre-baked rows.

    The actual SQL filter (``auto_enroll_new_workspaces.is_(True)``) is the
    contract under test, so the fake session must apply that filter rather
    than blindly returning everything — otherwise the test would pass for
    the wrong reason.
    """

    def __init__(self, installs: list[MCPConnectorInstall]) -> None:
        self._installs = installs
        self.upserts: list[dict[str, Any]] = []

    async def execute(self, stmt: Any) -> _FakeScalarResult:  # noqa: ARG002
        # Mirror the SQL WHERE clauses in ``enroll_workspace_in_org_wide_mcp``:
        # org_id match, workspace_id IS NULL, install_state == 'active',
        # auto_enroll_new_workspaces IS TRUE.
        filtered = [
            i
            for i in self._installs
            if i.workspace_id is None
            and i.install_state == "active"
            and i.auto_enroll_new_workspaces is True
        ]
        return _FakeScalarResult(filtered)


@pytest.mark.asyncio
async def test_bootstrap_hook_skips_install_with_auto_enroll_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``enroll_workspace_in_org_wide_mcp`` must skip installs flagged False.

    Belt-and-suspenders with the create-time test above: even if some
    future refactor regresses the create-side default, the bootstrap hook
    itself must not enroll an install whose flag was explicitly turned off.
    """
    install_off = MCPConnectorInstall(
        org_id="org-1",
        workspace_id=None,
        install_scope="org",
        template_id="mctpl-x",
        name="off-install",
        server_url="https://a.example.com/mcp",
        server_url_hash="hash-a",
        transport="streamable_http",
        auth_method="static",
        default_credential_policy="org",
        auth_status="pending",
        auto_enroll_new_workspaces=False,
        created_by_user_id="usr-1",
    )
    install_on = MCPConnectorInstall(
        org_id="org-1",
        workspace_id=None,
        install_scope="org",
        template_id="mctpl-y",
        name="on-install",
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

        async def get_active_by_identity(self, **_kwargs: Any) -> MCPConnector:
            return MCPConnector(
                id="mcpco-auto",
                org_id="org-1",
                template_id="mctpl-y",
                name="on-install",
                server_url="https://b.example.com/mcp",
                server_url_hash="hash-b",
                transport="streamable_http",
                auth_method="static",
                status="active",
                created_by_user_id="usr-1",
            )

    monkeypatch.setattr(
        "cubebox.mcp.workspace_bootstrap.MCPWorkspaceConnectorStateRepository",
        _FakeStateRepoBootstrap,
    )
    monkeypatch.setattr(
        "cubebox.mcp.workspace_bootstrap.MCPConnectorRepository",
        _FakeConnectorRepoBootstrap,
    )

    session = _FakeSession([install_off, install_on])
    await enroll_workspace_in_org_wide_mcp(
        session,  # type: ignore[arg-type]
        org_id="org-1",
        workspace_id="ws-new",
        actor_user_id="usr-1",
    )

    # Only the install with auto_enroll_new_workspaces=True should have
    # produced a state-row upsert for the new workspace.
    assert len(upserts) == 1, upserts
    assert upserts[0]["install_id"] == install_on.id
    assert upserts[0]["connector_id"] == "mcpco-auto"
    assert upserts[0]["workspace_id"] == "ws-new"
