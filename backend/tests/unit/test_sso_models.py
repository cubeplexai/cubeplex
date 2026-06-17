from cubebox.models.external_identity import ExternalIdentity
from cubebox.models.sso_connection import SSOConnection


def test_sso_connection_id_prefix() -> None:
    conn = SSOConnection(
        org_id="org-test",
        protocol="oidc",
        display_name="Test SSO",
        config={},
    )
    assert conn.id.startswith("sso-")


def test_external_identity_id_prefix() -> None:
    eid = ExternalIdentity(
        user_id="usr-test",
        provider_type="oidc_sso",
        provider_id="sso-test",
        external_id="ext-123",
        external_email="test@example.com",
    )
    assert eid.id.startswith("eid-")
