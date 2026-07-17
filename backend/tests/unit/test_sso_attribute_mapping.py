"""Unit tests for SSO attribute mapping."""

import pytest

from cubeplex.sso.attribute_mapping import (
    AttributeMappingError,
    apply_mapping,
)


def test_oidc_defaults() -> None:
    raw = {"sub": "user-123", "email": "a@b.com", "name": "Alice"}
    result = apply_mapping(raw, {}, protocol="oidc")
    assert result.id == "user-123"
    assert result.email == "a@b.com"
    assert result.name == "Alice"


def test_oidc_custom_mapping_overrides_defaults() -> None:
    raw = {"user_id": "u1", "mail": "a@b.com", "display": "Alice"}
    mapping = {"id": "user_id", "email": "mail", "name": "display"}
    result = apply_mapping(raw, mapping, protocol="oidc")
    assert result.id == "u1"
    assert result.email == "a@b.com"
    assert result.name == "Alice"


def test_saml_requires_explicit_mapping() -> None:
    raw = {"NameID": "u1", "email": "a@b.com"}
    with pytest.raises(AttributeMappingError, match="No mapping configured"):
        apply_mapping(raw, {}, protocol="saml")


def test_saml_with_full_mapping() -> None:
    raw = {
        "NameID": "uid-saml",
        "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress": "a@b.com",
        "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name": "Bob",
    }
    mapping = {
        "id": "NameID",
        "email": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
        "name": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name",
    }
    result = apply_mapping(raw, mapping, protocol="saml")
    assert result.id == "uid-saml"
    assert result.email == "a@b.com"
    assert result.name == "Bob"


def test_missing_required_attribute_raises() -> None:
    raw = {"sub": "u1"}  # missing email
    with pytest.raises(AttributeMappingError, match="email"):
        apply_mapping(raw, {}, protocol="oidc")


def test_list_value_takes_first_element() -> None:
    raw = {"sub": "u1", "email": ["a@b.com", "c@d.com"], "name": ["Alice"]}
    result = apply_mapping(raw, {}, protocol="oidc")
    assert result.email == "a@b.com"
    assert result.name == "Alice"


def test_name_optional() -> None:
    raw = {"sub": "u1", "email": "a@b.com"}
    result = apply_mapping(raw, {}, protocol="oidc")
    assert result.name is None


def test_empty_list_for_required_raises() -> None:
    raw = {"sub": "u1", "email": []}
    with pytest.raises(AttributeMappingError, match="email"):
        apply_mapping(raw, {}, protocol="oidc")
