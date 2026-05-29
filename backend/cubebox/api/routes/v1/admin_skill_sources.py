"""Org-admin management of remote skill sources (/admin/skill-sources)."""

from __future__ import annotations

import ipaddress
import socket
from typing import Annotated
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.auth.context import RequestContext
from cubebox.db import get_session
from cubebox.mcp.dependencies import get_admin_request_context
from cubebox.models import SkillSource
from cubebox.repositories.skill_source import SkillSourceRepository

router = APIRouter(prefix="/admin/skill-sources", tags=["admin-skill-sources"])

_TRUST_TIERS = {"official", "community", "untrusted"}

# Hostnames that name the local box or known-internal infra. Save-time
# rejection of these is a defense-in-depth measure on top of trusting org
# admins — in multi-tenant deployments org admins are tenant users, so
# blocking obvious SSRF targets here keeps `/search`, `/tree`, and `/raw`
# fetches off internal endpoints even if an admin tries.
_FORBIDDEN_HOSTNAMES = {
    "localhost",
    "ip6-localhost",
    "ip6-loopback",
    "metadata",
    "metadata.google.internal",
}
_FORBIDDEN_HOSTNAME_SUFFIXES = (".local", ".internal", ".localdomain")


def _validate_registry_base_url(raw: str) -> None:
    """Reject schemes/hosts that would turn skill discovery into SSRF.

    Raises ``HTTPException`` with detail ``BAD_BASE_URL`` for any URL that
    isn't plain http/https against a routable public host.
    """
    try:
        parsed = urlparse(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="BAD_BASE_URL") from exc
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(status_code=400, detail="BAD_BASE_URL")
    host = (parsed.hostname or "").lower()
    if not host:
        raise HTTPException(status_code=400, detail="BAD_BASE_URL")
    if host in _FORBIDDEN_HOSTNAMES:
        raise HTTPException(status_code=400, detail="BAD_BASE_URL")
    if any(host.endswith(suf) for suf in _FORBIDDEN_HOSTNAME_SUFFIXES):
        raise HTTPException(status_code=400, detail="BAD_BASE_URL")
    # IPv4 literal check covers both canonical dotted-quad ("127.0.0.1") and
    # the alternative forms Linux getaddrinfo accepts but ipaddress.ip_address
    # rejects: decimal int ("2130706433"), hex ("0x7f000001"), octal, and
    # short-dot ("127.1"). Each of those is a known SSRF-evasion form, so we
    # reject any host that inet_aton parses but doesn't re-serialize to the
    # canonical dotted-quad. Canonical IPv4 still has to pass `is_global`.
    try:
        packed_v4 = socket.inet_aton(host)
    except OSError:
        packed_v4 = None
    except ValueError as exc:
        # inet_aton raises ValueError (not OSError) for an embedded NUL in
        # the host, e.g. "127.0.0.1\x00". Treat malformed hosts as bad input
        # rather than letting it escape as a 500.
        raise HTTPException(status_code=400, detail="BAD_BASE_URL") from exc
    if packed_v4 is not None:
        canonical_v4 = ipaddress.IPv4Address(packed_v4)
        if host != str(canonical_v4) or not canonical_v4.is_global:
            raise HTTPException(status_code=400, detail="BAD_BASE_URL")
        return
    # Not an IPv4 literal in any form — try IPv6, otherwise treat as a
    # DNS name (DNS-based SSRF evasion is out of scope for v1; admins
    # are still trusted operators here).
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return
    if not ip.is_global:
        raise HTTPException(status_code=400, detail="BAD_BASE_URL")


class CreateSkillSourceRequest(BaseModel):
    name: str
    base_url: str
    repo: str | None = None
    trust_tier: str = "untrusted"


class PatchSkillSourceRequest(BaseModel):
    enabled: bool | None = None
    trust_tier: str | None = None


class SkillSourceResponse(BaseModel):
    id: str
    name: str
    kind: str
    base_url: str
    repo: str | None
    trust_tier: str
    enabled: bool


def _to_response(row: SkillSource) -> SkillSourceResponse:
    return SkillSourceResponse(
        id=row.id,
        name=row.name,
        kind=row.kind,
        base_url=row.base_url,
        repo=row.repo,
        trust_tier=row.trust_tier,
        enabled=row.enabled,
    )


@router.post("", status_code=201, response_model=SkillSourceResponse)
async def create_source(
    body: CreateSkillSourceRequest,
    *,
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SkillSourceResponse:
    if body.trust_tier not in _TRUST_TIERS:
        raise HTTPException(status_code=400, detail="BAD_TRUST_TIER")
    _validate_registry_base_url(body.base_url)
    row = await SkillSourceRepository(session).create(
        org_id=ctx.org_id,
        name=body.name,
        base_url=body.base_url,
        repo=body.repo,
        trust_tier=body.trust_tier,
        created_by_user_id=ctx.user.id,
    )
    return _to_response(row)


@router.get("", response_model=list[SkillSourceResponse])
async def list_sources(
    *,
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[SkillSourceResponse]:
    rows = await SkillSourceRepository(session).list_for_org(ctx.org_id)
    return [_to_response(r) for r in rows]


@router.patch("/{source_id}", response_model=SkillSourceResponse)
async def patch_source(
    source_id: str,
    body: PatchSkillSourceRequest,
    *,
    ctx: Annotated[RequestContext, Depends(get_admin_request_context)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SkillSourceResponse:
    # Validate all requested fields BEFORE mutating, so a 400 on trust_tier
    # never leaves a half-applied enabled flip behind.
    if body.trust_tier is not None and body.trust_tier not in _TRUST_TIERS:
        raise HTTPException(status_code=400, detail="BAD_TRUST_TIER")
    repo = SkillSourceRepository(session)
    if body.enabled is not None:
        if not await repo.set_enabled(ctx.org_id, source_id, body.enabled):
            raise HTTPException(status_code=404, detail="SOURCE_NOT_FOUND")
    if body.trust_tier is not None:
        if not await repo.set_trust_tier(ctx.org_id, source_id, body.trust_tier):
            raise HTTPException(status_code=404, detail="SOURCE_NOT_FOUND")
    row = await repo.get(ctx.org_id, source_id)
    if row is None:
        raise HTTPException(status_code=404, detail="SOURCE_NOT_FOUND")
    return _to_response(row)
