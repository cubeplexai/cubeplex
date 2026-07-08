from dataclasses import dataclass

from cubebox.services.mcp_ws_available import compute_available_rows


@dataclass
class _Install:
    id: str
    template_id: str | None
    install_scope: str
    workspace_id: str | None
    install_state: str = "active"


@dataclass
class _Template:
    id: str
    status: str = "active"


@dataclass
class _State:
    connector_id: str
    enabled: bool


def test_org_install_without_state_row_appears_as_no_state_row():
    org_installs = [_Install("mcpco-org-1", "mctpl-a", "org", None)]
    rows = compute_available_rows(
        ws_id="ws-1",
        org_installs=org_installs,
        ws_installs=[],
        ws_states=[],
        templates=[_Template("mctpl-a"), _Template("mctpl-b")],
    )
    org_rows = [r for r in rows if r.source == "org_install"]
    assert len(org_rows) == 1
    assert org_rows[0].reason == "no_state_row"
    assert org_rows[0].connector_id == "mcpco-org-1"


def test_org_install_with_disabled_state_row_appears_as_state_disabled():
    rows = compute_available_rows(
        ws_id="ws-1",
        org_installs=[_Install("mcpco-org-1", "mctpl-a", "org", None)],
        ws_installs=[],
        ws_states=[_State("mcpco-org-1", enabled=False)],
        templates=[_Template("mctpl-a")],
    )
    org_rows = [r for r in rows if r.source == "org_install"]
    assert len(org_rows) == 1
    assert org_rows[0].reason == "state_disabled"


def test_org_install_with_enabled_state_row_omitted():
    rows = compute_available_rows(
        ws_id="ws-1",
        org_installs=[_Install("mcpco-org-1", "mctpl-a", "org", None)],
        ws_installs=[],
        ws_states=[_State("mcpco-org-1", enabled=True)],
        templates=[_Template("mctpl-a")],
    )
    assert all(r.source != "org_install" for r in rows)


def test_template_already_installed_at_org_omitted():
    rows = compute_available_rows(
        ws_id="ws-1",
        org_installs=[_Install("mcpco-org-1", "mctpl-a", "org", None)],
        ws_installs=[],
        ws_states=[_State("mcpco-org-1", enabled=False)],
        templates=[_Template("mctpl-a")],
    )
    assert all(r.source != "template" for r in rows)


def test_template_already_installed_at_workspace_omitted():
    """Workspace-scope install of the same template must hide the template."""
    rows = compute_available_rows(
        ws_id="ws-1",
        org_installs=[],
        ws_installs=[_Install("mcins-ws-1", "mctpl-a", "workspace", "ws-1")],
        ws_states=[],
        templates=[_Template("mctpl-a")],
    )
    assert rows == []


def test_template_with_only_tombstoned_workspace_install_still_available():
    """Reinstall must stay one-click; tombstoned installs do not block."""
    rows = compute_available_rows(
        ws_id="ws-1",
        org_installs=[],
        ws_installs=[
            _Install("mcins-ws-1", "mctpl-a", "workspace", "ws-1", install_state="uninstalled")
        ],
        ws_states=[],
        templates=[_Template("mctpl-a")],
    )
    template_rows = [r for r in rows if r.source == "template"]
    assert len(template_rows) == 1
    assert template_rows[0].reason == "not_installed_at_org"
