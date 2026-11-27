"""Types for the artifact call bridge.

The bridge is the only surface agent-authored artifact HTML may call at
runtime. Functions are registered declaratively (see ``functions/``), so a new
capability is a handler module plus one registration — not a new route, not a
new client contract.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from cubeplex.credentials.encryption import EncryptionBackend
    from cubeplex.models import Artifact, Role


class ArtifactCallError(Exception):
    """Base error for bridge calls; the route maps these onto HTTP statuses."""


class ArtifactFunctionNotFound(ArtifactCallError):
    """The requested function name is not registered."""


class ArtifactCallInvalidInput(ArtifactCallError):
    """Arguments did not validate against the function's input model."""


class ArtifactCallRateLimited(ArtifactCallError):
    """The caller exceeded the per-minute budget for this function."""


class ArtifactCallFailed(ArtifactCallError):
    """The function ran but produced no result (provider error, timeout, disabled)."""


@dataclass(frozen=True)
class ArtifactCallContext:
    """Everything a bridge function may need to stay scoped and authorized.

    Built by the authenticated route today. A future share-token route builds
    the same object from a share token, so functions never learn which front
    door the call came through.
    """

    org_id: str
    workspace_id: str
    user_id: str
    role: Role
    conversation_id: str
    artifact: Artifact
    session: AsyncSession
    encryption_backend: EncryptionBackend | None = None
    tracer: Any | None = None


@dataclass(frozen=True)
class ArtifactFunction:
    """One callable capability exposed to artifact HTML."""

    name: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[[ArtifactCallContext, Any], Awaitable[dict[str, Any]]]
    rate_limit_per_minute: int | None = None
    timeout_seconds: float | None = None
