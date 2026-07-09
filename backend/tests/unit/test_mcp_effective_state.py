"""Pure-function tests for :func:`cubebox.mcp.effective.compute_effective_state`.

One test per row of plan §Task 5 Step 1's reason matrix. The pure function
is the contract for UI / runtime / admin diagnostics — each terminal reason
must remain distinct so consumers can branch on the string.
"""

from __future__ import annotations

from cubebox.mcp.effective import (
    MCPEffectiveInput,
    MCPGrantInput,
    compute_effective_state,
)


def _input(**overrides: object) -> MCPEffectiveInput:
    """Build an input with sensible defaults; override per-test fields."""
    defaults: dict[str, object] = {
        "template_status": "active",
        "install_present": True,
        "install_state": "active",
        "workspace_state_present": True,
        "workspace_enabled": True,
        "auth_method": "static",
        "auth_status": "connected",
        "discovery_status": "ok",
        "credential_policy": "org",
        "grant": MCPGrantInput(scope="org", status="valid", has_refresh=False),
        "transport": "streamable_http",
    }
    defaults.update(overrides)
    return MCPEffectiveInput(**defaults)  # type: ignore[arg-type]


def test_no_install_row_reports_not_installed() -> None:
    result = compute_effective_state(_input(install_present=False))
    assert result.usable is False
    assert result.reason == "not_installed"
    assert result.credential_availability == "missing"


def test_install_uninstalled_reports_install_uninstalled() -> None:
    result = compute_effective_state(_input(install_state="uninstalled"))
    assert result.usable is False
    assert result.reason == "install_uninstalled"


def test_template_disabled_is_hard_block() -> None:
    result = compute_effective_state(_input(template_status="disabled"))
    assert result.usable is False
    assert result.reason == "template_deprecated"


def test_template_deprecated_does_not_block() -> None:
    """Deprecated templates surface via the DTO field but stay usable."""
    result = compute_effective_state(_input(template_status="deprecated"))
    assert result.usable is True
    assert result.reason == "usable"
    assert result.credential_availability == "available"


def test_custom_install_skips_template_gate() -> None:
    """``template_status=None`` (custom install) must not block usability."""
    result = compute_effective_state(_input(template_status=None))
    assert result.usable is True
    assert result.reason == "usable"


def test_workspace_state_missing_reports_not_enabled() -> None:
    result = compute_effective_state(_input(workspace_state_present=False))
    assert result.usable is False
    assert result.reason == "not_enabled_in_workspace"


def test_workspace_disabled_reports_not_enabled() -> None:
    result = compute_effective_state(_input(workspace_enabled=False))
    assert result.usable is False
    assert result.reason == "not_enabled_in_workspace"


def test_no_auth_install_is_usable_with_not_required_credential() -> None:
    result = compute_effective_state(
        _input(
            auth_method="none",
            credential_policy="none",
            grant=None,
            auth_status="not_required",
        )
    )
    assert result.usable is True
    assert result.reason == "usable"
    assert result.credential_availability == "not_required"


def test_pending_oauth_at_org_scope() -> None:
    result = compute_effective_state(
        _input(
            auth_method="oauth",
            auth_status="pending",
            credential_policy="org",
            grant=None,
        )
    )
    assert result.usable is False
    assert result.reason == "pending_oauth"


def test_missing_org_grant_static_auth() -> None:
    """Static auth with no grant reports the scope-specific missing reason,
    not ``pending_oauth`` (which is OAuth-only)."""
    result = compute_effective_state(
        _input(
            auth_method="static",
            auth_status="pending",
            credential_policy="org",
            grant=None,
        )
    )
    assert result.usable is False
    assert result.reason == "missing_org_grant"


def test_missing_workspace_grant() -> None:
    result = compute_effective_state(
        _input(
            credential_policy="workspace",
            grant=None,
        )
    )
    assert result.usable is False
    assert result.reason == "missing_workspace_grant"


def test_user_policy_missing_grant_is_user_needs_connection() -> None:
    """User-policy installs always report user_needs_connection when their
    user grant is missing — even if the install row's auth_status is still
    'pending' from an abandoned admin flow."""
    result = compute_effective_state(
        _input(
            auth_method="oauth",
            auth_status="pending",
            credential_policy="user",
            grant=None,
        )
    )
    assert result.usable is False
    assert result.reason == "user_needs_connection"


def test_grant_expired_without_refresh_reports_grant_expired() -> None:
    result = compute_effective_state(
        _input(
            credential_policy="org",
            grant=MCPGrantInput(scope="org", status="expired", has_refresh=False),
        )
    )
    assert result.usable is False
    assert result.reason == "grant_expired"


def test_grant_expired_with_refresh_reports_grant_expired() -> None:
    result = compute_effective_state(
        _input(
            credential_policy="org",
            grant=MCPGrantInput(scope="org", status="expired", has_refresh=True),
        )
    )
    assert result.usable is False
    assert result.reason == "grant_expired"


def test_cross_scope_grant_treated_as_missing_for_policy_scope() -> None:
    """An org grant must NOT flip a user-policy install to usable."""
    result = compute_effective_state(
        _input(
            credential_policy="user",
            grant=MCPGrantInput(scope="org", status="valid", has_refresh=False),
        )
    )
    assert result.usable is False
    assert result.reason == "user_needs_connection"


def test_discovery_failed_after_auth_gates_pass() -> None:
    result = compute_effective_state(_input(discovery_status="error"))
    assert result.usable is False
    assert result.reason == "discovery_failed"


def test_valid_user_grant_is_usable() -> None:
    result = compute_effective_state(
        _input(
            credential_policy="user",
            grant=MCPGrantInput(scope="user", status="valid", has_refresh=True),
        )
    )
    assert result.usable is True
    assert result.reason == "usable"
    assert result.credential_availability == "available"


def test_no_auth_install_skips_credential_policy_check() -> None:
    """``auth_method=='none'`` short-circuits before grant resolution even
    if ``credential_policy`` was (erroneously) set to a non-none value.

    The pure function does not police the API-layer invariant that a
    no-auth install must have credential_policy='none'; if the row reaches
    the runtime in that shape, the connector still runs without a grant.
    """
    result = compute_effective_state(
        _input(
            auth_method="none",
            credential_policy="org",  # API-layer bug but runtime tolerates it.
            grant=None,
        )
    )
    assert result.usable is True
    assert result.reason == "usable"
    assert result.credential_availability == "not_required"
