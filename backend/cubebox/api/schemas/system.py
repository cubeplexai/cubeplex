"""Schemas for /api/v1/system/* endpoints."""

from typing import Literal

from pydantic import BaseModel


class SystemInfoResponse(BaseModel):
    deployment_mode: Literal["single_tenant", "multi_tenant"]
    version: str
    needs_org_setup: bool


class SetupRequest(BaseModel):
    org_name: str
    slug: str


class SetupResponse(BaseModel):
    org_id: str
    workspace_id: str
