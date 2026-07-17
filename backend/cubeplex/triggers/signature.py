"""HMAC signature generation and verification for webhook authentication."""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime


def sign(secret: str, timestamp: str, raw_body: bytes) -> str:
    """Generate HMAC-SHA256 signature for a webhook payload.

    Args:
        secret: The webhook secret used for signing.
        timestamp: Unix epoch seconds as a string.
        raw_body: The raw HTTP request body.

    Returns:
        Hex-encoded SHA256 HMAC signature.
    """
    msg = f"{timestamp}.".encode() + raw_body
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def verify(secret: str, timestamp: str, raw_body: bytes, provided: str) -> bool:
    """Verify a webhook signature using constant-time comparison.

    Args:
        secret: The webhook secret.
        timestamp: Unix epoch seconds as a string.
        raw_body: The raw HTTP request body.
        provided: The signature provided by the client.

    Returns:
        True if the signature is valid, False otherwise.
    """
    return hmac.compare_digest(sign(secret, timestamp, raw_body), provided)


def verify_with_rotation(
    *,
    current: str,
    previous: str | None,
    previous_expires_at: datetime | None,
    timestamp: str,
    raw_body: bytes,
    provided: str,
    now: datetime,
) -> bool:
    """Verify a webhook signature with support for secret rotation.

    Attempts to verify the signature with the current secret first.
    If that fails and a previous secret is set, checks if the signature
    is valid with the previous secret within its expiration window.

    Args:
        current: The current webhook secret.
        previous: The previous webhook secret (if rotating).
        previous_expires_at: Expiration time for the previous secret.
        timestamp: Unix epoch seconds as a string.
        raw_body: The raw HTTP request body.
        provided: The signature provided by the client.
        now: The current datetime (allows for testing time-sensitive logic).

    Returns:
        True if the signature verifies with either secret, False otherwise.
    """
    if verify(current, timestamp, raw_body, provided):
        return True

    if previous is None or previous_expires_at is None:
        return False

    if now >= previous_expires_at:
        return False

    return verify(previous, timestamp, raw_body, provided)


def timestamp_fresh(timestamp: str, *, now: datetime, max_age_seconds: int = 300) -> bool:
    """Check if a timestamp is within the allowed freshness window.

    Args:
        timestamp: Unix epoch seconds as a string.
        now: The current datetime.
        max_age_seconds: Maximum age in seconds (default 300s = 5 minutes).

    Returns:
        True if the timestamp is within the freshness window, False otherwise.
    """
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False

    now_epoch = int(now.timestamp())
    return abs(now_epoch - ts) <= max_age_seconds
