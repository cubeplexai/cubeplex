"""Request/response schemas for admin sandbox policy + workspace sandbox status."""

from typing import Any, Literal

from pydantic import BaseModel


class SandboxPolicyOut(BaseModel):
    default_image: str
    network_default_action: Literal["allow", "deny"] = "deny"
    network_rules: list[dict[str, Any]] = []
    command_rules: list[dict[str, Any]] = []
    egress_proxy: str | None = None
    # Resource limits as Kubernetes quantity strings. null = the org hasn't
    # overridden the system default (sandbox.resource.* for cpu/memory; the
    # cluster StorageClass default for storage).
    resource_cpu: str | None = None
    resource_memory: str | None = None
    storage: str | None = None
    # OQ-6 soft-conflict warnings (e.g. deny rule covers an installed
    # credential's required host). Empty on GET and on a clean PUT.
    warnings: list[str] = []


class UpdateSandboxPolicyIn(BaseModel):
    default_image: str
    network_default_action: Literal["allow", "deny"] = "deny"
    network_rules: list[dict[str, Any]] | None = None
    command_rules: list[dict[str, Any]] | None = None
    egress_proxy: str | None = None
    resource_cpu: str | None = None
    resource_memory: str | None = None
    storage: str | None = None


SandboxStatusValue = Literal[
    "provisioning",
    "running",
    "pausing",
    "paused",
    "resuming",
    "terminated",
    "failed",
    "kill_pending",
    # Route-level sentinel: ``get_sandbox_status`` returns this when no active
    # row exists for the scope. Never stored on a UserSandbox row.
    "absent",
]


class SandboxStatusOut(BaseModel):
    """Workspace-scope read-only sandbox status payload.

    ``status='absent'`` means the caller has no active sandbox row in this
    workspace. ``browser_url`` is reserved for the future live-view feature
    and is ``None`` in v1.
    """

    status: SandboxStatusValue
    default_image: str | None
    last_activity_at: str | None
    browser_url: str | None
