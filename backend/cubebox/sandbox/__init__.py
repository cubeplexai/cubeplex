"""Sandbox execution module"""

from cubebox.sandbox.base import SandboxError
from cubebox.sandbox.opensandbox import OpenSandbox

__all__ = ["OpenSandbox", "SandboxError"]

# SandboxManager is imported lazily to avoid circular imports.
# Use cubebox.sandbox.manager.get_sandbox_manager() to access.
