"""Feishu webhook signature + verification-token + encrypted-payload helpers.

Reference: ~/hermes-agent/gateway/platforms/feishu.py:3362 (validated against
real Feishu webhook traffic). Algorithm: SHA256(timestamp + nonce +
encrypt_key + body). Headers: x-lark-request-timestamp, x-lark-request-nonce,
x-lark-signature.

The verification_token is the *second* auth layer Feishu provides; it lives
in the webhook payload's `header.token` (or top-level `token` on legacy
events). Always compare in constant time. Run the verification-token check
BEFORE handling `url_verification` — otherwise an attacker can prove
endpoint control by getting their supplied challenge echoed back.

When the Feishu app has "Event Encryption" enabled (separate console
toggle from signing), the entire POST body arrives as
``{"encrypt": "<base64 AES-256-CBC ciphertext>"}``. The key is
``SHA256(encrypt_key).digest()`` (32 bytes), IV is the first 16 bytes of
the decoded ciphertext, the rest is PKCS#7-padded plaintext JSON. Decrypt
BEFORE running any signature/token check — the decrypted body is what
those checks run on.
"""

import base64
import hashlib
import hmac
import json
from typing import Any


class FeishuSignatureError(Exception):
    """Raised when a Feishu request fails signature or token validation."""


def verify_verification_token(*, expected: str, incoming: str) -> None:
    """Constant-time compare of the verification token (second auth layer)."""
    if not expected or not incoming or not hmac.compare_digest(expected, incoming):
        raise FeishuSignatureError("invalid verification token")


def decrypt_feishu_payload(
    *,
    encrypt_key: str,
    encrypted_b64: str,
) -> dict[str, Any]:
    """Decrypt a Feishu encrypted webhook body.

    Feishu's encryption mode wraps the whole payload as
    ``{"encrypt": "<base64 ciphertext>"}``. Algorithm: AES-256-CBC with
    key = SHA256(encrypt_key); IV = first 16 bytes of the ciphertext;
    body = PKCS#7-padded JSON. Returns the parsed dict on success;
    raises ``FeishuSignatureError`` on any decode/decrypt/parse failure.
    """
    if not encrypt_key:
        raise FeishuSignatureError("encrypt_key required to decrypt payload")
    try:
        # Local import — pycryptodome is a lark_oapi transitive dep so the
        # process always has it, but keep the surface minimal at module load.
        from Crypto.Cipher import AES
    except ImportError as exc:  # pragma: no cover — dep guaranteed at runtime
        raise FeishuSignatureError("pycryptodome not installed") from exc
    try:
        raw = base64.b64decode(encrypted_b64)
    except (ValueError, TypeError) as exc:
        raise FeishuSignatureError("invalid base64 in encrypted payload") from exc
    if len(raw) < 32 or (len(raw) - 16) % 16 != 0:
        raise FeishuSignatureError("encrypted payload length invalid")
    key = hashlib.sha256(encrypt_key.encode()).digest()
    iv, ciphertext = raw[:16], raw[16:]
    try:
        plaintext = AES.new(key, AES.MODE_CBC, iv).decrypt(ciphertext)
    except Exception as exc:
        raise FeishuSignatureError("AES decrypt failed") from exc
    # PKCS#7 unpad. The last byte is the padding length.
    pad = plaintext[-1] if plaintext else 0
    if pad < 1 or pad > 16 or plaintext[-pad:] != bytes([pad]) * pad:
        raise FeishuSignatureError("invalid PKCS#7 padding")
    body = plaintext[:-pad]
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise FeishuSignatureError("decrypted payload is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise FeishuSignatureError("decrypted payload is not a JSON object")
    return parsed


def verify_feishu_signature(
    *,
    encrypt_key: str,
    raw_body: bytes,
    timestamp: str,
    nonce: str,
    signature: str,
) -> None:
    """Validate x-lark-signature HMAC. Raises on failure."""
    if not timestamp or not nonce or not signature:
        raise FeishuSignatureError("missing signature headers")
    try:
        body_str = raw_body.decode("utf-8", errors="replace")
    except Exception as exc:
        raise FeishuSignatureError("body not decodable") from exc
    payload = f"{timestamp}{nonce}{encrypt_key}{body_str}".encode()
    expected = hashlib.sha256(payload).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise FeishuSignatureError("signature mismatch")
