# deploy/kubernetes/egress-bundle/webhook/tests/test_cert_minter.py
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from webhook.cert_minter import CertMinter, load_ca


def _make_ca(tmp_path):
    # produce a throwaway CA key+cert for the test
    from webhook.cert_minter import generate_ca
    key_pem, cert_pem = generate_ca("cubebox-egress-test-ca")
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
