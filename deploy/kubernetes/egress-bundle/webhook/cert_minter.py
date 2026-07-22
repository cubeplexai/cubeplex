# deploy/kubernetes/egress-bundle/webhook/cert_minter.py
"""Mint short-lived per-sandbox client certs (CN=sandbox_id) signed by a fixed CA."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


@dataclass
class CA:
    key: ec.EllipticCurvePrivateKey
    cert: x509.Certificate


def generate_ca(common_name: str) -> tuple[bytes, bytes]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return key_pem, cert.public_bytes(serialization.Encoding.PEM)


def load_ca(key_pem: bytes, cert_pem: bytes) -> CA:
    key = serialization.load_pem_private_key(key_pem, password=None)
    cert = x509.load_pem_x509_certificate(cert_pem)
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise TypeError("CA key must be an EC private key")
    return CA(key=key, cert=cert)


def mint_server_cert(
    ca: CA, *, common_name: str, sans: list[str], days: int = 365
) -> tuple[bytes, bytes]:
    """Mint a server leaf cert (with SANs, for TLS hostname verification) signed
    by `ca`. Used for the webhook's own serving cert and the backend's mTLS
    listener cert — distinct from `CertMinter.mint`, which mints CN-only client
    certs for per-sandbox mTLS auth (no hostname to verify there)."""
    key = ec.generate_private_key(ec.SECP256R1())
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
        .issuer_name(ca.cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(s) for s in sans]), critical=False
        )
        .sign(ca.key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return key_pem, cert.public_bytes(serialization.Encoding.PEM)


class CertMinter:
    def __init__(self, ca: CA) -> None:
        self._ca = ca

    def mint(self, *, sandbox_id: str, ttl_minutes: int) -> tuple[bytes, bytes]:
        key = ec.generate_private_key(ec.SECP256R1())
        now = dt.datetime.now(dt.timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, sandbox_id)]))
            .issuer_name(self._ca.cert.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=2))
            .not_valid_after(now + dt.timedelta(minutes=ttl_minutes))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(self._ca.key, hashes.SHA256())
        )
        key_pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        return key_pem, cert.public_bytes(serialization.Encoding.PEM)
