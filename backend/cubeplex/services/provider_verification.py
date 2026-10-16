"""Immutable probe inputs and current-result persistence contracts."""

from __future__ import annotations

from dataclasses import dataclass, field

from cubeplex.llm.config import ProviderConfig


@dataclass(frozen=True)
class ProbeInput:
    provider_id: str
    model_db_id: str
    model_id: str
    configuration_fingerprint: str
    provider_fingerprint: str
    probe_generation: int
    provider_probe_generation: int
    provider_evidence_revision: int
    model_evidence_revision: int
    config: ProviderConfig = field(repr=False)
