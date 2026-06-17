"""Generate SAML test fixtures.

Run once; committed output is checked in. Re-running regenerates a fresh
keypair, so the committed Response XML must be regenerated too — they are
a self-consistent set::

    cd backend && uv run python tests/fixtures/sso/saml/generate.py

Produces:
- ``idp_key.pem`` — IdP signing private key (gitignored — only needed at
  fixture-generation time; tests verify signatures with the public cert).
- ``idp_cert.pem`` — IdP signing certificate (public).
- ``response_with_email.xml`` — signed Response, attribute ``email`` present.
- ``response_no_email.xml`` — signed Response, no ``email`` attribute.
- ``response_wrong_inresponseto.xml`` — signed Response with mismatched
  ``InResponseTo`` (still signed correctly).
- ``idp_metadata.xml`` — minimal IdP metadata document.

Fixed values (matched by tests):

- AuthnRequest ID expected by SP: ``ONELOGIN_request_id``
- Wrong ID embedded in tampered fixture: ``ONELOGIN_wrong_id``
- IdP entityId: ``https://idp.example.com/saml/metadata``
- IdP SSO URL: ``https://idp.example.com/saml/sso``
- SP entityId: ``https://sp.example.com/saml/metadata``
- SP ACS URL: ``https://sp.example.com/saml/acs``
- NameID: ``user@example.com`` (emailAddress format)
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from onelogin.saml2.utils import OneLogin_Saml2_Utils

FIXTURE_DIR = Path(__file__).resolve().parent

REQUEST_ID = "ONELOGIN_request_id"
WRONG_REQUEST_ID = "ONELOGIN_wrong_id"
IDP_ENTITY_ID = "https://idp.example.com/saml/metadata"
IDP_SSO_URL = "https://idp.example.com/saml/sso"
SP_ENTITY_ID = "https://sp.example.com/saml/metadata"
SP_ACS_URL = "https://sp.example.com/saml/acs"
NAMEID = "user@example.com"


def _generate_keypair() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "idp.example.com"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Cubebox Test IdP"),
        ]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=3650))
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode("utf-8")
    return key_pem, cert_pem


def _cert_body(cert_pem: str) -> str:
    """Strip PEM headers — what goes inside <ds:X509Certificate>."""
    return "".join(line for line in cert_pem.splitlines() if not line.startswith("-----"))


def _build_assertion_xml(*, in_response_to: str, include_email: bool) -> str:
    """Build standalone unsigned Assertion XML."""
    assertion_id = "_assertion_" + in_response_to
    issue_instant = "2020-01-01T00:00:00Z"
    not_before = "2020-01-01T00:00:00Z"
    not_on_or_after = "2099-01-01T01:00:00Z"

    email_attr = (
        f"""<saml:Attribute Name="email" NameFormat="urn:oasis:names:tc:SAML:2.0:attrname-format:basic">
              <saml:AttributeValue xsi:type="xs:string">{NAMEID}</saml:AttributeValue>
            </saml:Attribute>"""
        if include_email
        else ""
    )

    return f"""<saml:Assertion xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xs="http://www.w3.org/2001/XMLSchema" ID="{assertion_id}" Version="2.0" IssueInstant="{issue_instant}">
    <saml:Issuer>{IDP_ENTITY_ID}</saml:Issuer>
    <saml:Subject>
      <saml:NameID Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress">{NAMEID}</saml:NameID>
      <saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer">
        <saml:SubjectConfirmationData NotOnOrAfter="{not_on_or_after}" Recipient="{SP_ACS_URL}" InResponseTo="{in_response_to}"/>
      </saml:SubjectConfirmation>
    </saml:Subject>
    <saml:Conditions NotBefore="{not_before}" NotOnOrAfter="{not_on_or_after}">
      <saml:AudienceRestriction>
        <saml:Audience>{SP_ENTITY_ID}</saml:Audience>
      </saml:AudienceRestriction>
    </saml:Conditions>
    <saml:AuthnStatement AuthnInstant="{issue_instant}" SessionIndex="_session_{in_response_to}">
      <saml:AuthnContext>
        <saml:AuthnContextClassRef>urn:oasis:names:tc:SAML:2.0:ac:classes:PasswordProtectedTransport</saml:AuthnContextClassRef>
      </saml:AuthnContext>
    </saml:AuthnStatement>
    <saml:AttributeStatement>
      <saml:Attribute Name="displayName" NameFormat="urn:oasis:names:tc:SAML:2.0:attrname-format:basic">
        <saml:AttributeValue xsi:type="xs:string">User Example</saml:AttributeValue>
      </saml:Attribute>
      {email_attr}
    </saml:AttributeStatement>
  </saml:Assertion>"""


def _wrap_in_response(signed_assertion_xml: str, *, in_response_to: str) -> str:
    """Wrap a signed Assertion in an unsigned Response envelope."""
    response_id = "_response_" + in_response_to
    issue_instant = "2020-01-01T00:00:00Z"
    return f"""<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" ID="{response_id}" Version="2.0" IssueInstant="{issue_instant}" Destination="{SP_ACS_URL}" InResponseTo="{in_response_to}">
  <saml:Issuer>{IDP_ENTITY_ID}</saml:Issuer>
  <samlp:Status>
    <samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/>
  </samlp:Status>
  {signed_assertion_xml}
</samlp:Response>"""


def _sign(xml: str, key_pem: str, cert_pem: str) -> str:
    """Sign root element via python3-saml's add_sign (returns str)."""
    out = OneLogin_Saml2_Utils.add_sign(
        xml,
        key_pem,
        cert_pem,
        sign_algorithm="http://www.w3.org/2001/04/xmldsig-more#rsa-sha256",
        digest_algorithm="http://www.w3.org/2001/04/xmlenc#sha256",
    )
    if isinstance(out, bytes):
        out = out.decode("utf-8")
    # add_sign re-serializes with an XML declaration; drop it so we can
    # nest the signed assertion inside the response.
    if out.startswith("<?xml"):
        out = out.split("?>", 1)[1].lstrip()
    return out


def _build_signed_response(
    *,
    in_response_to: str,
    include_email: bool,
    key_pem: str,
    cert_pem: str,
) -> bytes:
    """Sign Assertion first, embed in Response envelope, sign Response."""
    assertion = _build_assertion_xml(in_response_to=in_response_to, include_email=include_email)
    signed_assertion = _sign(assertion, key_pem, cert_pem)
    response = _wrap_in_response(signed_assertion, in_response_to=in_response_to)
    signed_response = _sign(response, key_pem, cert_pem)
    return signed_response.encode("utf-8")


def _build_metadata_xml(cert_body: str) -> str:
    return f"""<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata" entityID="{IDP_ENTITY_ID}">
  <md:IDPSSODescriptor protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol" WantAuthnRequestsSigned="false">
    <md:KeyDescriptor use="signing">
      <ds:KeyInfo xmlns:ds="http://www.w3.org/2000/09/xmldsig#">
        <ds:X509Data>
          <ds:X509Certificate>{cert_body}</ds:X509Certificate>
        </ds:X509Data>
      </ds:KeyInfo>
    </md:KeyDescriptor>
    <md:SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect" Location="{IDP_SSO_URL}"/>
  </md:IDPSSODescriptor>
</md:EntityDescriptor>"""


def main() -> None:
    key_pem, cert_pem = _generate_keypair()
    cert_body = _cert_body(cert_pem)

    (FIXTURE_DIR / "idp_key.pem").write_text(key_pem)
    (FIXTURE_DIR / "idp_cert.pem").write_text(cert_pem)

    for name, request_id, include_email in (
        ("response_with_email.xml", REQUEST_ID, True),
        ("response_no_email.xml", REQUEST_ID, False),
        ("response_wrong_inresponseto.xml", WRONG_REQUEST_ID, True),
    ):
        signed = _build_signed_response(
            in_response_to=request_id,
            include_email=include_email,
            key_pem=key_pem,
            cert_pem=cert_pem,
        )
        # python3-saml's process_response expects base64.
        encoded = base64.b64encode(signed).decode("ascii")
        (FIXTURE_DIR / name).write_text(encoded)

    metadata = _build_metadata_xml(cert_body)
    (FIXTURE_DIR / "idp_metadata.xml").write_text(metadata)


if __name__ == "__main__":
    main()
