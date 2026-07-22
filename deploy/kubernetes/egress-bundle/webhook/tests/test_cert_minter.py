# deploy/kubernetes/egress-bundle/webhook/tests/test_cert_minter.py
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from webhook.cert_minter import CertMinter, load_ca, mint_server_cert


def _make_ca(tmp_path):
    # produce a throwaway CA key+cert for the test
    from webhook.cert_minter import generate_ca
    key_pem, cert_pem = generate_ca("cubeplex-egress-test-ca")
    return load_ca(key_pem, cert_pem)


def test_minted_cert_has_sandbox_id_cn_and_chains_to_ca(tmp_path):
    ca = _make_ca(tmp_path)
    minter = CertMinter(ca)
    key_pem, cert_pem = minter.mint(sandbox_id="sbx-123", ttl_minutes=60)
    cert = x509.load_pem_x509_certificate(cert_pem)
    cn = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)[0].value
    assert cn == "sbx-123"
    # verify signature chains to the CA public key
    ca.cert.public_key().verify(
        cert.signature, cert.tbs_certificate_bytes,
        ec.ECDSA(cert.signature_hash_algorithm),
    )


def test_server_cert_has_cn_sans_ec_key_and_chains_to_ca(tmp_path):
    ca = _make_ca(tmp_path)
    sans = ["cubeplex-egress-webhook.cubeplex.svc", "cubeplex-egress-webhook.cubeplex.svc.cluster.local"]
    key_pem, cert_pem = mint_server_cert(
        ca, common_name="cubeplex-egress-webhook.cubeplex.svc", sans=sans
    )
    key = serialization.load_pem_private_key(key_pem, password=None)
    assert isinstance(key, ec.EllipticCurvePrivateKey)

    cert = x509.load_pem_x509_certificate(cert_pem)
    cn = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)[0].value
    assert cn == "cubeplex-egress-webhook.cubeplex.svc"

    san_ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    assert san_ext.value.get_values_for_type(x509.DNSName) == sans

    # verify signature chains to the CA public key (same requirement load_ca enforces on read)
    ca.cert.public_key().verify(
        cert.signature, cert.tbs_certificate_bytes,
        ec.ECDSA(cert.signature_hash_algorithm),
    )
