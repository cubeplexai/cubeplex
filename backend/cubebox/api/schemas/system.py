"""Schemas for /api/v1/system/* endpoints."""

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")


class SystemInfoResponse(BaseModel):
    deployment_mode: Literal["single_tenant", "multi_tenant"]
    version: str
    needs_org_setup: bool


class SetupRequest(BaseModel):
    org_name: str = Field(min_length=2, max_length=64)
    slug: str = Field(min_length=1, max_length=32)

    @field_validator("slug")
    @classmethod
    def _check_slug(cls, v: str) -> str:
        if len(v) < 3:
            raise ValueError("slug_too_short")
        if not _SLUG_RE.match(v):
            raise ValueError("slug_invalid_format")
        return v


class SetupResponse(BaseModel):
    org_id: str
    workspace_id: str
