"""Catalog loader: YAML → validated, flattened catalog. Spec §4."""

from __future__ import annotations

from cubepi.providers.capability import CapabilityDescriptor

from cubebox.llm.catalog.types import Endpoint, Region


def compose_base_url(regions: dict[str, Region], endpoint: Endpoint) -> str:
    """base_url = (endpoint.host || regions[endpoint.region].host) + endpoint.path.

    A full ``endpoint.base_url`` bypasses composition entirely (§4.1).
    """
    if endpoint.base_url is not None:
        return endpoint.base_url
    host = endpoint.host
    if host is None:
        region = regions.get(endpoint.region)
        if region is None:
            raise ValueError(f"endpoint references unknown region {endpoint.region!r}")
        host = region.host
    return host + endpoint.path


def preset_key_for(vendor: str, endpoint: Endpoint) -> str:
    """preset_key = vendor/region/protocol[/plan], or endpoint.key override (§4.4)."""
    if endpoint.key is not None:
        return endpoint.key
    parts = [vendor, endpoint.region, endpoint.protocol]
    if endpoint.plan is not None:
        parts.append(endpoint.plan)
    return "/".join(parts)


def resolve_capability(
    ref: str | dict[str, object], profiles: dict[str, dict[str, object]]
) -> CapabilityDescriptor:
    """A scalar string is a profile reference; a mapping is inline (§4.3).

    An unknown profile name fails loudly (not a silent empty descriptor).
    """
    if isinstance(ref, str):
        if ref not in profiles:
            raise ValueError(f"unknown capability profile {ref!r}")
        return CapabilityDescriptor.model_validate(profiles[ref])
    return CapabilityDescriptor.model_validate(ref)
