"""Schemas for /api/v1/system/* endpoints."""

from typing import Literal

from pydantic import BaseModel


class SystemInfoResponse(BaseModel):
    deployment_mode: Literal["single_tenant", "multi_tenant"]
    version: str
    # Whether sandbox support is enabled; gates sandbox-only UI (e.g. the
    # browser live-view button) so it isn't shown where it can't work.
    sandbox_enabled: bool = False
    # Active password policy preset ("low" | "high"). Frontend mirrors the
    # rules for pre-submit UX; the backend remains authoritative.
    password_policy: Literal["low", "high"] = "high"
    # Edition is computed server-side from the configured license key; the
    # frontend never holds license logic — it only mirrors these two fields.
    edition: Literal["oss", "ee"] = "oss"
    features: list[str] = []
    # The origin the backend tells identity providers to call back on, from
    # `public_base_url`. The admin SSO form must build its copy-paste IdP URLs
    # from this, not from the browser's origin: those differ whenever the SPA is
    # served from somewhere other than the API (local dev is :3000 vs :8000), and
    # a redirect_uri the IdP does not recognise fails the login.
    public_base_url: str = ""
