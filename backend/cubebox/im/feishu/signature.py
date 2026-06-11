"""Feishu webhook signature + verification-token validation.

Reference: ~/hermes-agent/gateway/platforms/feishu.py:3362 (validated against
real Feishu webhook traffic). Algorithm: SHA256(timestamp + nonce +
encrypt_key + body). Headers: x-lark-request-timestamp, x-lark-request-nonce,
x-lark-signature.

The verification_token is the *second* auth layer Feishu provides; it lives
in the webhook payload's `header.token` (or top-level `token` on legacy
events). Always compare in constant time. Run the verification-token check
BEFORE handling `url_verification` — otherwise an attacker can prove
endpoint control by getting their supplied challenge echoed back.
"""

import hashlib
import hmac


class FeishuSignatureError(Exception):
    """Raised when a Feishu request fails signature or token validation."""


def verify_verification_token(*, expected: str, incoming: str) -> None:
    """Constant-time compare of the verification token (second auth layer)."""
    if not expected or not incoming or not hmac.compare_digest(expected, incoming):
        raise FeishuSignatureError("invalid verification token")


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
