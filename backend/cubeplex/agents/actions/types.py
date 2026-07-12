"""Core types for the agent platform actions mechanism."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

# --- Domain exceptions (raised by services, mapped by front doors) ---


class ActionNotFound(Exception):
    """Target entity does not exist or is soft-deleted."""


class ActionPermissionDenied(Exception):
    """Caller lacks the required role (e.g. not owner or admin)."""


class ActionInvalidInput(Exception):
    """Validation failure (bad cron, missing field, etc.)."""


# --- Registry types ---


@dataclass(frozen=True)
class AgentOperation:
    """One operation within a capability (e.g. 'create', 'list')."""

    name: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[..., Awaitable[Any]]
    mutates: bool = False


@dataclass(frozen=True)
class AgentCapability:
    """A named group of operations exposed as a single agent tool.

    ``always_mutable`` opts a capability out of the global ``allow_mutations``
    gate so its mutating ops survive even on automated runs (schedule fires,
    IM ingress). Use sparingly — the gate exists to defang prompt injection
    on non-interactive triggers. Scheduled tasks intentionally opt in: a
    schedule that fires must be able to reschedule or cancel itself, and IM
    users expect ``remind me at …`` to work the same as on the web.
    """

    name: str
    description: str
    operations: list[AgentOperation] = field(default_factory=list)
    always_mutable: bool = False
