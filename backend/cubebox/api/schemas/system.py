"""Schemas for /api/v1/system/* endpoints."""

from typing import Literal

from pydantic import BaseModel


class SystemInfoResponse(BaseModel):
    deployment_mode: Literal["single_tenant", "multi_tenant"]
    version: str
    # Whether sandbox support is enabled; gates sandbox-only UI (e.g. the
    # browser live-view button) so it isn't shown where it can't work.
    sandbox_enabled: bool = False
