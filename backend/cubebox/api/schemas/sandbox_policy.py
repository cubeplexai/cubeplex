"""Request/response schemas for admin sandbox policy."""

from typing import Any

from pydantic import BaseModel


class SandboxPolicyOut(BaseModel):
    default_image: str
    network_rules: list[dict[str, Any]] = []
    command_rules: list[dict[str, Any]] = []
    # OQ-6 soft-conflict warnings (e.g. deny rule covers an installed
    # credential's required host). Empty on GET and on a clean PUT.
    warnings: list[str] = []


class UpdateSandboxPolicyIn(BaseModel):
    default_image: str
    network_rules: list[dict[str, Any]] | None = None
    command_rules: list[dict[str, Any]] | None = None
