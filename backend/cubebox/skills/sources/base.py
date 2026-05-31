"""Candidate shape, trust tiers, the SkillRegistryAdapter protocol, and the opaque
candidate-id codec.

candidate_id is a URL-safe base64 token over "{kind}|{source_id}|{source_ref}".
It is the only handle clients pass back to preview/install, so a slash-laden
remote source_ref (a GitHub repo subpath) never has to fit a FastAPI path
segment. source_id is the registered remote SkillRegistry row id (empty for
local candidates). Stateless: decode recovers (kind, source_id, source_ref)
without any server lookup.

Both source_id and source_ref may not contain the `|` delimiter; row ids and
GitHub subpaths never do, so we split on the first two `|` only.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, Protocol

SourceKind = Literal["local", "remote"]


class TrustTier(StrEnum):
    official = "official"
    community = "community"
    untrusted = "untrusted"


class CandidateIdError(ValueError):
    """Raised when a candidate_id cannot be decoded."""


def encode_candidate_id(kind: SourceKind, source_ref: str, *, source_id: str = "") -> str:
    payload = f"{kind}|{source_id}|{source_ref}".encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def decode_candidate_id(candidate_id: str) -> tuple[SourceKind, str, str]:
    pad = "=" * (-len(candidate_id) % 4)
    try:
        raw = base64.urlsafe_b64decode(candidate_id + pad).decode()
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise CandidateIdError(f"undecodable candidate_id: {candidate_id!r}") from exc
    parts = raw.split("|", 2)
    if len(parts) != 3 or parts[0] not in ("local", "remote"):
        raise CandidateIdError(f"malformed candidate_id payload: {raw!r}")
    kind, source_id, source_ref = parts
    return kind, source_id, source_ref  # type: ignore[return-value]


@dataclass(frozen=True)
class SkillCandidate:
    """One normalized discovery result across any source.

    name           — human-facing display name (remote: upstream slug).
    canonical_name — the name load_skill resolves: local catalog name, or for a
                     not-yet-imported remote skill the name import WILL mint
                     (<org-slug>:<skill-slug>), computed up front.
    """

    candidate_id: str
    name: str
    canonical_name: str
    description: str
    source_kind: SourceKind
    source_ref: str
    keywords: list[str] = field(default_factory=list)
    version: str | None = None
    trust: TrustTier = TrustTier.untrusted
    install_state: Literal["enabled", "in_catalog", "available"] = "available"
    stars: int | None = None
    install_count: int | None = None
    source_name: str = "catalog"
    repo: str | None = None


class SkillRegistryAdapter(Protocol):
    kind: SourceKind

    async def search(self, query: str, *, limit: int) -> list[SkillCandidate]: ...

    async def fetch(self, source_ref: str) -> dict[str, bytes]:
        """Return {rel_path: bytes} of the skill bundle for import."""
        ...

    def trust_for_ref(self, source_ref: str) -> TrustTier:
        """Return the effective trust tier for this specific skill source_ref.

        For registries where all skills share one tier this is always the
        registry-level tier.  For skills.sh the tier depends on the upstream
        GitHub org (some orgs are official, others are community).
        """
        ...
