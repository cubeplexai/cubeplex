"""Unit tests for Feishu webhook signature + verification-token (Task 3)."""

import hashlib

import pytest

from cubebox.im.feishu.signature import (
    FeishuSignatureError,
    decrypt_feishu_payload,
    verify_feishu_signature,
    verify_verification_token,
)

ENCRYPT_KEY = "my-encrypt-key-32-chars-min------"
VTOKEN = "v-token-from-feishu-dashboard"


def _sign(*, ts: str, nonce: str, body: bytes) -> str:
    return hashlib.sha256(f"{ts}{nonce}{ENCRYPT_KEY}{body.decode()}".encode()).hexdigest()


def test_valid_signature_passes() -> None:
    body = b'{"schema":"2.0"}'
    verify_feishu_signature(
        encrypt_key=ENCRYPT_KEY,
        raw_body=body,
        timestamp="1700000000",
        nonce="abc",
        signature=_sign(ts="1700000000", nonce="abc", body=body),
    )


def test_tampered_body_rejected() -> None:
    body = b'{"schema":"2.0"}'
    sig = _sign(ts="1700000000", nonce="abc", body=body)
    with pytest.raises(FeishuSignatureError):
        verify_feishu_signature(
            encrypt_key=ENCRYPT_KEY,
            raw_body=b'{"evil":true}',
            timestamp="1700000000",
            nonce="abc",
            signature=sig,
        )


def test_tampered_timestamp_rejected() -> None:
    body = b'{"schema":"2.0"}'
    sig = _sign(ts="1700000000", nonce="abc", body=body)
    with pytest.raises(FeishuSignatureError):
        verify_feishu_signature(
            encrypt_key=ENCRYPT_KEY,
            raw_body=body,
            timestamp="1700000001",
            nonce="abc",
            signature=sig,
        )


def test_missing_headers_rejected() -> None:
    with pytest.raises(FeishuSignatureError):
        verify_feishu_signature(
            encrypt_key=ENCRYPT_KEY,
            raw_body=b"{}",
            timestamp="",
            nonce="abc",
            signature="x",
        )


def _encrypt(plaintext: bytes, encrypt_key: str, *, iv: bytes | None = None) -> str:
    """Reverse of decrypt_feishu_payload, used to fabricate test inputs."""
    import base64
    import hashlib
    import secrets as _secrets

    from Crypto.Cipher import AES

    key = hashlib.sha256(encrypt_key.encode()).digest()
    iv = iv or _secrets.token_bytes(16)
    pad = 16 - (len(plaintext) % 16)
    padded = plaintext + bytes([pad]) * pad
    ct = AES.new(key, AES.MODE_CBC, iv).encrypt(padded)
    return base64.b64encode(iv + ct).decode()


def test_decrypt_feishu_payload_roundtrip() -> None:
    body = b'{"header":{"event_id":"x","event_type":"im.message.receive_v1"},"event":{}}'
    encrypted = _encrypt(body, ENCRYPT_KEY)
    out = decrypt_feishu_payload(encrypt_key=ENCRYPT_KEY, encrypted_b64=encrypted)
    assert out["header"]["event_id"] == "x"


def test_decrypt_feishu_payload_wrong_key_fails() -> None:
    body = b'{"a":1}'
    encrypted = _encrypt(body, ENCRYPT_KEY, iv=b"\0" * 16)
    with pytest.raises(FeishuSignatureError):
        decrypt_feishu_payload(encrypt_key="different-key", encrypted_b64=encrypted)


def test_decrypt_feishu_payload_bad_base64_fails() -> None:
    with pytest.raises(FeishuSignatureError):
        decrypt_feishu_payload(encrypt_key=ENCRYPT_KEY, encrypted_b64="not-base64!!!")


def test_decrypt_feishu_payload_too_short_fails() -> None:
    import base64

    short = base64.b64encode(b"x" * 8).decode()
    with pytest.raises(FeishuSignatureError):
        decrypt_feishu_payload(encrypt_key=ENCRYPT_KEY, encrypted_b64=short)


def test_verification_token_constant_time_compare() -> None:
    verify_verification_token(expected=VTOKEN, incoming=VTOKEN)
    with pytest.raises(FeishuSignatureError):
        verify_verification_token(expected=VTOKEN, incoming="other")
    with pytest.raises(FeishuSignatureError):
        verify_verification_token(expected=VTOKEN, incoming="")
    with pytest.raises(FeishuSignatureError):
        verify_verification_token(expected="", incoming=VTOKEN)
