"""Catalog loader: YAML → validated, flattened catalog. Spec §4."""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

import yaml
from cubepi.providers.capability import CapabilityDescriptor

from cubeplex.llm.catalog.types import (
    Catalog,
    Endpoint,
    ModelPreset,
    Region,
    ResolvedEndpoint,
    Vendor,
)

_DATA_DIR = Path(__file__).parent / "data"


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


def _validate_plan_consistency(v: Vendor) -> None:
    tagged = [e.plan is not None for e in v.endpoints] + [m.plan is not None for m in v.models]
    if any(tagged) and not all(tagged):
        raise ValueError(
            f"vendor {v.vendor!r} may not mix plan-tagged and untagged endpoints/models"
        )


def _models_for(v: Vendor, endpoint: Endpoint) -> list[ModelPreset]:
    if endpoint.plan is None:  # untagged vendor -> every endpoint serves every model
        return list(v.models)
    return [m for m in v.models if endpoint.plan in (m.plans() or [])]


def build_catalog(
    raw_vendors: list[dict[str, Any] | Vendor], profiles: dict[str, dict[str, object]]
) -> Catalog:
    vendors = [v if isinstance(v, Vendor) else Vendor.model_validate(v) for v in raw_vendors]
    endpoints: dict[str, ResolvedEndpoint] = {}
    for v in vendors:
        _validate_plan_consistency(v)
        # unreachable-model check first: every model's plan(s) must hit some endpoint.
        # (Done before the per-endpoint dangling check so a model with no endpoint is
        # reported as unreachable rather than incidentally as a dangling endpoint.)
        ep_plans = {e.plan for e in v.endpoints}
        for m in v.models:
            mplans = m.plans()
            if mplans is not None and not (set(mplans) & ep_plans):
                raise ValueError(
                    f"vendor {v.vendor!r} model {m.model_id!r} plan(s) {mplans} "
                    f"match no endpoint (unreachable)"
                )
        # (region, protocol, plan) tuple uniqueness — enforced INDEPENDENTLY of
        # preset_key, because a `key:` override would otherwise let two identical
        # tuples through the preset_key dedup (spec §4.2).
        seen_tuples: set[tuple[str, str, str | None]] = set()
        for ep in v.endpoints:
            tup = (ep.region, ep.protocol, ep.plan)
            if tup in seen_tuples:
                raise ValueError(f"vendor {v.vendor!r} duplicate endpoint tuple {tup!r}")
            seen_tuples.add(tup)
            models = _models_for(v, ep)
            if ep.plan is not None and not models:
                raise ValueError(
                    f"vendor {v.vendor!r} endpoint plan {ep.plan!r} matches no model (dangling)"
                )
            key = preset_key_for(v.vendor, ep)
            if key in endpoints:
                raise ValueError(f"duplicate preset_key {key!r}")
            endpoints[key] = ResolvedEndpoint(
                preset_key=key,
                vendor=v.vendor,
                region=ep.region,
                protocol=ep.protocol,
                plan=ep.plan,
                base_url=compose_base_url(v.regions, ep),
                capability=resolve_capability(ep.capability, profiles),
                models=models,
            )
    return Catalog(vendors=vendors, endpoints=endpoints)


@cache
def load_catalog() -> Catalog:
    vendors_raw = yaml.safe_load((_DATA_DIR / "vendors.yaml").read_text("utf-8"))
    profiles = yaml.safe_load((_DATA_DIR / "capabilities.yaml").read_text("utf-8"))
    if not isinstance(vendors_raw, list):
        raise ValueError("vendors.yaml must be a top-level list")
    return build_catalog(vendors_raw, profiles or {})
