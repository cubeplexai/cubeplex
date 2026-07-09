from dataclasses import dataclass

from cubebox.services.mcp_admin_connectors import derive_admin_org_effective


@dataclass
class _Install:
    id: str
    auth_method: str
    auth_status: str
    discovery_status: str
    default_credential_policy: str


@dataclass
class _Grant:
    grant_status: str
    refresh_credential_id: str | None


def test_org_policy_with_valid_grant_is_usable():
    install = _Install("mcins-1", "oauth", "authorized", "ok", "org")
    grant = _Grant("valid", None)
    out = derive_admin_org_effective(install, grant)
    assert out.usable is True
    assert out.reason == "usable"
    assert out.credential_availability == "available"


def test_workspace_policy_install_skips_grant_check():
    install = _Install("mcins-1", "static", "pending", "ok", "workspace")
    out = derive_admin_org_effective(install, org_grant=None)
    assert out.usable is True
    assert out.reason == "usable"
    assert out.credential_availability is None  # creds live below org level


def test_user_policy_install_with_discovery_error():
    install = _Install("mcins-1", "static", "pending", "error", "user")
    out = derive_admin_org_effective(install, org_grant=None)
    assert out.usable is False
    assert out.reason == "discovery_failed"
    assert out.credential_availability is None


def test_none_auth_method_is_usable_regardless_of_policy():
    install = _Install("mcins-1", "none", "not_required", "ok", "none")
    out = derive_admin_org_effective(install, org_grant=None)
    assert out.usable is True
    assert out.reason == "usable"
    assert out.credential_availability == "not_required"


def test_org_policy_missing_grant_oauth_pending():
    install = _Install("mcins-1", "oauth", "pending", "not_run", "org")
    out = derive_admin_org_effective(install, org_grant=None)
    assert out.usable is False
    assert out.reason == "pending_oauth"
    assert out.credential_availability == "missing"


def test_org_policy_missing_grant_static():
    install = _Install("mcins-1", "static", "pending", "not_run", "org")
    out = derive_admin_org_effective(install, org_grant=None)
    assert out.usable is False
    assert out.reason == "missing_org_grant"
    assert out.credential_availability == "missing"


def test_org_policy_grant_expired_no_refresh():
    install = _Install("mcins-1", "oauth", "authorized", "ok", "org")
    grant = _Grant("expired", None)
    out = derive_admin_org_effective(install, grant)
    assert out.usable is False
    assert out.reason == "grant_expired"


def test_org_policy_grant_expired_with_refresh_requires_reconnect():
    install = _Install("mcins-1", "oauth", "authorized", "ok", "org")
    grant = _Grant("expired", "cred-refresh-1")
    out = derive_admin_org_effective(install, grant)
    assert out.usable is False
    assert out.reason == "grant_expired"


def test_org_policy_discovery_error_after_auth_gates_pass():
    install = _Install("mcins-1", "oauth", "authorized", "error", "org")
    grant = _Grant("valid", None)
    out = derive_admin_org_effective(install, grant)
    assert out.usable is False
    assert out.reason == "discovery_failed"
