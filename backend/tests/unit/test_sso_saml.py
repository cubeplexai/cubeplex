"""Unit tests for cubebox.sso.saml."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from cubebox.models.sso_connection import SSOConnection
from cubebox.sso.saml import (
    build_authn_request_url,
    generate_sp_metadata,
    parse_idp_metadata_xml,
    validate_response,
)

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "sso" / "saml"

IDP_ENTITY_ID = "https://idp.example.com/saml/metadata"
IDP_SSO_URL = "https://idp.example.com/saml/sso"
SP_ENTITY_ID = "https://sp.example.com/saml/metadata"
SP_ACS_URL = "https://sp.example.com/saml/acs"
REQUEST_ID = "ONELOGIN_request_id"
NAMEID = "user@example.com"


def _cert_body() -> str:
    cert = (FIXTURE_DIR / "idp_cert.pem").read_text()
    return "".join(line for line in cert.splitlines() if not line.startswith("-----"))


def _make_connection() -> SSOConnection:
    return SSOConnection(
        org_id="org-test",
        protocol="saml",
        display_name="Acme SAML",
        status="active",
        provisioning="auto",
        config={
            "idp_entity_id": IDP_ENTITY_ID,
            "idp_sso_url": IDP_SSO_URL,
            "idp_certificate": _cert_body(),
        },
    )


def _make_request_data(saml_response_b64: str) -> dict[str, object]:
    """Minimal request_data dict consumed by python3-saml's Auth ctor."""
    return {
        "https": "on",
        "http_host": "sp.example.com",
        "script_name": "/saml/acs",
        "server_port": "443",
        "get_data": {},
        "post_data": {"SAMLResponse": saml_response_b64},
    }


def _empty_request_data() -> dict[str, object]:
    return {
        "https": "on",
        "http_host": "sp.example.com",
        "script_name": "/saml/login",
        "server_port": "443",
        "get_data": {},
        "post_data": {},
    }


# ----------------------------------------------------------------------
# build_authn_request_url
# ----------------------------------------------------------------------


def test_build_authn_request_url_contains_samlrequest_and_relay_state() -> None:
    conn = _make_connection()
    relay = "state-token-abc"
    url, request_id = build_authn_request_url(
        conn,
        sp_entity_id=SP_ENTITY_ID,
        sp_acs_url=SP_ACS_URL,
        relay_state=relay,
        request_data=_empty_request_data(),
    )

    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "idp.example.com"
    query = parse_qs(parsed.query)
    assert "SAMLRequest" in query
    assert query["RelayState"] == [relay]
    assert request_id  # non-empty


# ----------------------------------------------------------------------
# validate_response
# ----------------------------------------------------------------------


def test_validate_response_with_email_attribute_marks_signed() -> None:
    conn = _make_connection()
    saml = (FIXTURE_DIR / "response_with_email.xml").read_text()

    info = validate_response(
        conn,
        sp_entity_id=SP_ENTITY_ID,
        sp_acs_url=SP_ACS_URL,
        request_data=_make_request_data(saml),
        expected_in_response_to=REQUEST_ID,
    )

    assert info.name_id == NAMEID
    assert info.email == NAMEID
    assert info.email_from_signed_attribute is True
    assert info.name == "User Example"
    assert info.attributes is not None
    assert info.attributes["email"] == [NAMEID]


def test_validate_response_without_email_falls_back_to_nameid() -> None:
    """Critical: when no `email` attribute is asserted, we must NOT claim
    the email is verified — otherwise a NameID that looks like another
    user's address could take over that account."""
    conn = _make_connection()
    saml = (FIXTURE_DIR / "response_no_email.xml").read_text()

    info = validate_response(
        conn,
        sp_entity_id=SP_ENTITY_ID,
        sp_acs_url=SP_ACS_URL,
        request_data=_make_request_data(saml),
        expected_in_response_to=REQUEST_ID,
    )

    assert info.name_id == NAMEID
    assert info.email == NAMEID  # fell back to NameID
    assert info.email_from_signed_attribute is False
    assert info.name == "User Example"


def test_validate_response_rejects_mismatched_in_response_to() -> None:
    """If the SAMLResponse's InResponseTo doesn't match what we issued,
    we must reject — this blocks unsolicited / IdP-initiated assertions
    and replay of responses meant for a different AuthnRequest."""
    conn = _make_connection()
    saml = (FIXTURE_DIR / "response_wrong_inresponseto.xml").read_text()

    with pytest.raises(ValueError, match="SAML validation failed"):
        validate_response(
            conn,
            sp_entity_id=SP_ENTITY_ID,
            sp_acs_url=SP_ACS_URL,
            request_data=_make_request_data(saml),
            expected_in_response_to=REQUEST_ID,
        )


# ----------------------------------------------------------------------
# generate_sp_metadata
# ----------------------------------------------------------------------


def test_generate_sp_metadata_includes_entity_id_and_acs() -> None:
    conn = _make_connection()
    md = generate_sp_metadata(conn, sp_entity_id=SP_ENTITY_ID, sp_acs_url=SP_ACS_URL)
    assert SP_ENTITY_ID in md
    assert SP_ACS_URL in md
    assert "EntityDescriptor" in md


# ----------------------------------------------------------------------
# parse_idp_metadata_xml
# ----------------------------------------------------------------------


def test_parse_idp_metadata_xml_extracts_three_fields() -> None:
    md = (FIXTURE_DIR / "idp_metadata.xml").read_text()
    parsed = parse_idp_metadata_xml(md)
    assert parsed["idp_entity_id"] == IDP_ENTITY_ID
    assert parsed["idp_sso_url"] == IDP_SSO_URL
    assert parsed["idp_certificate"]  # non-empty
