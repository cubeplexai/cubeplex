"""Unit tests for the pure catalog composition module.

Uses duck-typed dataclass stand-ins — no ORM, no session.
"""

from __future__ import annotations

from dataclasses import dataclass

from cubebox.services.mcp_catalog import (
    build_admin_catalog_rows,
    build_workspace_catalog_rows,
)

# ---------------------------------------------------------------------------
# Duck-typed stand-ins
# ---------------------------------------------------------------------------


@dataclass
class _Template:
    id: str
    name: str
    visibility: str = "public"


@dataclass
class _Connector:
    id: str
    template_id: str
    discovery_status: str = "ok"
    auto_enroll_new_workspaces: bool = False


@dataclass
class _Grant:
    grant_status: str  # 'valid' | 'expired'


@dataclass
class _State:
    connector_id: str
    enabled: bool


# ---------------------------------------------------------------------------
# Admin catalog tests
# ---------------------------------------------------------------------------


def test_admin_rows_cover_all_visible_templates_with_facts() -> None:
    """3 templates, each with distinct facts — verify row fields + order."""
    t_a = _Template("tpl-a", "Alpha")
    t_b = _Template("tpl-b", "Beta")
    t_c = _Template("tpl-c", "Gamma")

    # connector for Alpha (valid grant, 2 enabled workspaces) → in_use=True
    conn_a = _Connector("con-a", "tpl-a")
    # connector for Beta (expired grant) → needs_attention=True
    conn_b = _Connector("con-b", "tpl-b")
    # Gamma has no connector → in_use=False

    rows = build_admin_catalog_rows(
        templates=[t_a, t_b, t_c],
        connectors_by_template_id={"tpl-a": conn_a, "tpl-b": conn_b},
        disabled_template_ids=set(),
        enabled_counts_by_connector_id={"con-a": 2, "con-b": 0},
        org_grants_by_connector_id={
            "con-a": _Grant("valid"),
            "con-b": _Grant("expired"),
        },
        eligible_workspace_count=5,
    )

    assert len(rows) == 3

    # Locate rows by template id
    by_id = {r.template.id: r for r in rows}

    row_a = by_id["tpl-a"]
    assert row_a.in_use is True
    assert row_a.org_grant_status == "valid"
    assert row_a.enabled_workspace_count == 2
    assert row_a.eligible_workspace_count == 5
    assert row_a.needs_attention is False
    assert row_a.disabled is False

    row_b = by_id["tpl-b"]
    assert row_b.in_use is True
    assert row_b.org_grant_status == "expired"
    assert row_b.needs_attention is True  # expired grant

    row_c = by_id["tpl-c"]
    assert row_c.in_use is False
    assert row_c.connector is None
    assert row_c.org_grant_status is None
    assert row_c.enabled_workspace_count == 0
    assert row_c.needs_attention is False


def test_admin_rows_discovery_error_needs_attention() -> None:
    """Connector with discovery_status='error' → needs_attention=True."""
    t = _Template("tpl-err", "Error Tool")
    conn = _Connector("con-err", "tpl-err", discovery_status="error")

    rows = build_admin_catalog_rows(
        templates=[t],
        connectors_by_template_id={"tpl-err": conn},
        disabled_template_ids=set(),
        enabled_counts_by_connector_id={},
        org_grants_by_connector_id={"con-err": _Grant("valid")},
        eligible_workspace_count=3,
    )

    assert len(rows) == 1
    row = rows[0]
    assert row.connector is not None
    assert row.connector.discovery_status == "error"
    assert row.org_grant_status == "valid"
    assert row.needs_attention is True


def test_admin_rows_auto_enroll_passthrough() -> None:
    """auto_enroll_new_workspaces=True on connector → row reflects True."""
    t = _Template("tpl-enroll", "Auto Enroll Tool")
    conn = _Connector("con-enroll", "tpl-enroll", auto_enroll_new_workspaces=True)

    rows = build_admin_catalog_rows(
        templates=[t],
        connectors_by_template_id={"tpl-enroll": conn},
        disabled_template_ids=set(),
        enabled_counts_by_connector_id={},
        org_grants_by_connector_id={},
        eligible_workspace_count=3,
    )

    assert len(rows) == 1
    assert rows[0].auto_enroll_new_workspaces is True


def test_admin_disabled_flag_passthrough() -> None:
    """Template in disabled_template_ids → row.disabled=True."""
    t = _Template("tpl-x", "X Tool")
    conn = _Connector("con-x", "tpl-x")

    rows = build_admin_catalog_rows(
        templates=[t],
        connectors_by_template_id={"tpl-x": conn},
        disabled_template_ids={"tpl-x"},
        enabled_counts_by_connector_id={},
        org_grants_by_connector_id={},
        eligible_workspace_count=3,
    )

    assert len(rows) == 1
    assert rows[0].disabled is True


def test_admin_rows_ordering() -> None:
    """in-use rows first, then by template.name.lower()."""
    t_no_conn = _Template("tpl-no", "Zeal")  # no connector → not in_use
    t_in_use_b = _Template("tpl-b", "Beta")  # connector → in_use
    t_in_use_a = _Template("tpl-a", "Alpha")  # connector → in_use

    conn_b = _Connector("con-b", "tpl-b")
    conn_a = _Connector("con-a", "tpl-a")

    rows = build_admin_catalog_rows(
        templates=[t_no_conn, t_in_use_b, t_in_use_a],
        connectors_by_template_id={"tpl-b": conn_b, "tpl-a": conn_a},
        disabled_template_ids=set(),
        enabled_counts_by_connector_id={},
        org_grants_by_connector_id={},
        eligible_workspace_count=1,
    )

    names = [r.template.name for r in rows]
    # in_use first (Alpha < Beta), then not-in-use (Zeal)
    assert names == ["Alpha", "Beta", "Zeal"]


# ---------------------------------------------------------------------------
# Workspace catalog tests
# ---------------------------------------------------------------------------


def test_workspace_rows_exclude_org_disabled() -> None:
    """Templates in disabled_template_ids must be absent from workspace rows."""
    t_ok = _Template("tpl-ok", "Good Tool")
    t_disabled = _Template("tpl-dis", "Disabled Tool")
    conn_ok = _Connector("con-ok", "tpl-ok")

    rows = build_workspace_catalog_rows(
        templates=[t_ok, t_disabled],
        connectors_by_template_id={"tpl-ok": conn_ok},
        states_by_connector_id={},
        disabled_template_ids={"tpl-dis"},
    )

    ids = {r.template.id for r in rows}
    assert "tpl-dis" not in ids
    assert "tpl-ok" in ids


def test_workspace_enabled_comes_from_state_row() -> None:
    """no state row → False; state.enabled=False → False; True → True."""
    t_no_state = _Template("tpl-ns", "No State")
    t_dis_state = _Template("tpl-ds", "Disabled State")
    t_en_state = _Template("tpl-es", "Enabled State")

    conn_ns = _Connector("con-ns", "tpl-ns")
    conn_ds = _Connector("con-ds", "tpl-ds")
    conn_es = _Connector("con-es", "tpl-es")

    rows = build_workspace_catalog_rows(
        templates=[t_no_state, t_dis_state, t_en_state],
        connectors_by_template_id={
            "tpl-ns": conn_ns,
            "tpl-ds": conn_ds,
            "tpl-es": conn_es,
        },
        states_by_connector_id={
            "con-ds": _State("con-ds", enabled=False),
            "con-es": _State("con-es", enabled=True),
        },
        disabled_template_ids=set(),
    )

    by_id = {r.template.id: r for r in rows}
    assert by_id["tpl-ns"].enabled is False  # no state row
    assert by_id["tpl-ds"].enabled is False  # state disabled
    assert by_id["tpl-es"].enabled is True  # state enabled


def test_workspace_rows_ordering() -> None:
    """enabled first, then by template.name.lower()."""
    t_z = _Template("tpl-z", "Zeta")
    t_a = _Template("tpl-a", "Alpha")
    t_b = _Template("tpl-b", "Beta")

    conn_z = _Connector("con-z", "tpl-z")
    conn_a = _Connector("con-a", "tpl-a")
    conn_b = _Connector("con-b", "tpl-b")

    rows = build_workspace_catalog_rows(
        templates=[t_z, t_a, t_b],
        connectors_by_template_id={
            "tpl-z": conn_z,
            "tpl-a": conn_a,
            "tpl-b": conn_b,
        },
        states_by_connector_id={
            "con-z": _State("con-z", enabled=True),
        },
        disabled_template_ids=set(),
    )

    names = [r.template.name for r in rows]
    # enabled first: Zeta; then disabled by name: Alpha, Beta
    assert names == ["Zeta", "Alpha", "Beta"]
