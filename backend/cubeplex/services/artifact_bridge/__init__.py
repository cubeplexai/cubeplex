"""Artifact call bridge: named, validated functions callable from artifact HTML.

Front door today is the authenticated workspace route in
``api/routes/v1/artifacts.py``. Adding a capability means adding a module under
``functions/`` — limits, validation, and error translation are shared here.
"""

from cubeplex.services.artifact_bridge.dispatch import dispatch
from cubeplex.services.artifact_bridge.registry import (
    catalogue,
    get_function,
    register_function,
)
from cubeplex.services.artifact_bridge.types import (
    ArtifactCallContext,
    ArtifactCallError,
    ArtifactCallFailed,
    ArtifactCallInvalidInput,
    ArtifactCallRateLimited,
    ArtifactFunction,
    ArtifactFunctionNotFound,
)

__all__ = [
    "ArtifactCallContext",
    "ArtifactCallError",
    "ArtifactCallFailed",
    "ArtifactCallInvalidInput",
    "ArtifactCallRateLimited",
    "ArtifactFunction",
    "ArtifactFunctionNotFound",
    "catalogue",
    "dispatch",
    "get_function",
    "register_function",
]
