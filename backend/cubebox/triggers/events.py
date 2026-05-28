"""NormalizedEvent envelope + dedup key derivation (pure)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class NormalizedEvent:
    """Normalized event envelope from any source (webhook, schedule, IM, etc)."""

    event_id: str
    source_type: str
    trigger_id: str
    event_type: str | None
    occurred_at: datetime | None
    subject: str | None
    payload: dict[str, Any]
    dedup_key: str


def derive_dedup_key(raw_body: bytes, event_id_header: str | None) -> str:
    """Stable idempotency key for an inbound event.

    Uses the provider event-id header when truthy; otherwise falls back
    to SHA-256 of the raw body bytes. The fallback intentionally does
    NOT include the signed timestamp — a re-signed identical body must
    yield the same key so a provider replay doesn't spawn a duplicate run.
    """
    if event_id_header:
        return event_id_header
    return hashlib.sha256(raw_body).hexdigest()
