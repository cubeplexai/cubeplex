"""Sandbox execution module"""

from cubebox.sandbox.opensandbox import OpenSandbox

__all__ = ["OpenSandbox"]

# SandboxManager is imported lazily to avoid circular imports.
# Use cubebox.sandbox.manager.get_sandbox_manager() to access.
