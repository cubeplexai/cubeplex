"""SAML Service Provider — build AuthnRequest and validate Response."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from onelogin.saml2.auth import OneLogin_Saml2_Auth
from onelogin.saml2.idp_metadata_parser import OneLogin_Saml2_IdPMetadataParser
from onelogin.saml2.settings import OneLogin_Saml2_Settings

from cubeplex.models.sso_connection import SSOConnection


@dataclass(frozen=True)
class SAMLUserInfo:
    """Result of validating a SAML Response.

    ``email_from_signed_attribute`` is True only when ``email`` came from a
    signed attribute in the SAML assertion. False when we fell back to
    ``name_id`` (Persistent/Transient NameID formats are opaque identifiers,
    not addresses). The ACS handler must NOT pass ``email_verified=True``
    when this is False — otherwise an attacker controlling a NameID that
    looks like another user's email can take over the account.
    """

    name_id: str
    email: str
    email_from_signed_attribute: bool
    name: str | None = None
    attributes: dict[str, Any] | None = None


def _build_saml_settings(
    connection: SSOConnection,
    *,
    sp_entity_id: str,
    sp_acs_url: str,
) -> dict[str, Any]:
    """Build python3-saml settings dict from SSOConnection config."""
    cfg = connection.config
    return {
        "strict": True,
        "debug": False,
        "sp": {
            "entityId": sp_entity_id,
            "assertionConsumerService": {
                "url": sp_acs_url,
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
            },
        },
        "idp": {
            "entityId": cfg["idp_entity_id"],
            "singleSignOnService": {
                "url": cfg["idp_sso_url"],
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
            },
            "x509cert": cfg["idp_certificate"],
        },
        "security": {
            "nameIdEncrypted": False,
            "authnRequestsSigned": False,
            "wantMessagesSigned": True,
            "wantAssertionsSigned": False,
            "wantNameIdEncrypted": False,
            # Reject unsolicited (IdP-initiated) assertions — SP-initiated only.
            "rejectUnsolicitedResponsesWithInResponseTo": True,
            "signatureAlgorithm": "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256",
            "digestAlgorithm": "http://www.w3.org/2001/04/xmlenc#sha256",
        },
    }


def build_authn_request_url(
    connection: SSOConnection,
    *,
    sp_entity_id: str,
    sp_acs_url: str,
    relay_state: str,
    request_data: dict[str, Any],
) -> tuple[str, str]:
    """Build SAML AuthnRequest redirect URL.

    Returns ``(redirect_url, request_id)``. The caller passes the already
    issued state token as ``relay_state`` so the signed AuthnRequest isn't
    invalidated by post-hoc URL rewriting. ``request_id`` is stored in the
    signed state so the ACS handler can verify the SAMLResponse's
    ``InResponseTo`` matches — blocking unsolicited / IdP-initiated
    assertions.
    """
    settings = _build_saml_settings(connection, sp_entity_id=sp_entity_id, sp_acs_url=sp_acs_url)
    auth = OneLogin_Saml2_Auth(request_data, settings)
    url = auth.login(return_to=relay_state)
    request_id = auth.get_last_request_id()
    return url, request_id


def validate_response(
    connection: SSOConnection,
    *,
    sp_entity_id: str,
    sp_acs_url: str,
    request_data: dict[str, Any],
    expected_in_response_to: str,
) -> SAMLUserInfo:
    """Validate SAML Response and extract user info.

    ``expected_in_response_to`` is the AuthnRequest ID issued at
    ``build_authn_request_url`` time (stored in the signed state). The
    underlying library is told to require it via ``process_response``.
    """
    settings = _build_saml_settings(connection, sp_entity_id=sp_entity_id, sp_acs_url=sp_acs_url)
    auth = OneLogin_Saml2_Auth(request_data, settings)
    auth.process_response(request_id=expected_in_response_to)
    errors = auth.get_errors()
    if errors:
        reason = auth.get_last_error_reason()
        raise ValueError(f"SAML validation failed: {', '.join(errors)} — {reason}")
    if not auth.is_authenticated():
        raise ValueError("SAML response: user not authenticated")

    name_id = auth.get_nameid()
    attributes = auth.get_attributes()

    email_from_signed_attribute = False
    email_values = attributes.get("email")
    if email_values and email_values[0]:
        email = email_values[0]
        email_from_signed_attribute = True
    else:
        # NameID is only an email when NameIDFormat is emailAddress AND the
        # IdP is configured to use it as such. We surface it for routing /
        # display but mark email_from_signed_attribute=False so Identity
        # Resolution refuses to auto-link by email.
        email = name_id

    name: str | None = None
    for key in ("displayName", "name"):
        values = attributes.get(key)
        if values and values[0]:
            name = values[0]
            break

    return SAMLUserInfo(
        name_id=name_id,
        email=email,
        email_from_signed_attribute=email_from_signed_attribute,
        name=name,
        attributes=dict(attributes),
    )


def generate_sp_metadata(
    connection: SSOConnection,
    *,
    sp_entity_id: str,
    sp_acs_url: str,
) -> str:
    """Generate SP metadata XML for the IdP to consume."""
    settings_dict = _build_saml_settings(
        connection, sp_entity_id=sp_entity_id, sp_acs_url=sp_acs_url
    )
    settings = OneLogin_Saml2_Settings(settings_dict, sp_validation_only=True)
    metadata = settings.get_sp_metadata()
    if isinstance(metadata, bytes):
        return metadata.decode("utf-8")
    return str(metadata)


def parse_idp_metadata_xml(xml: str) -> dict[str, Any]:
    """Parse IdP metadata XML and extract config fields."""
    parsed = OneLogin_Saml2_IdPMetadataParser.parse(xml)
    idp = parsed.get("idp", {})
    return {
        "idp_entity_id": idp.get("entityId", ""),
        "idp_sso_url": idp.get("singleSignOnService", {}).get("url", ""),
        "idp_certificate": idp.get("x509cert", ""),
    }
