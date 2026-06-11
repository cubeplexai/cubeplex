"""Unit tests for Feishu webhook signature + verification-token (Task 3)."""

import hashlib

import pytest

from cubebox.im.feishu.signature import (
    FeishuSignatureError,
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


def test_verification_token_constant_time_compare() -> None:
    verify_verification_token(expected=VTOKEN, incoming=VTOKEN)
    with pytest.raises(FeishuSignatureError):
        verify_verification_token(expected=VTOKEN, incoming="other")
    with pytest.raises(FeishuSignatureError):
        verify_verification_token(expected=VTOKEN, incoming="")
    with pytest.raises(FeishuSignatureError):
        verify_verification_token(expected="", incoming=VTOKEN)
