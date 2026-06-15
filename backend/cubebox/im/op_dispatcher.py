"""OpDispatcher protocol -- platform-specific outbound rendering.

Each platform implements this protocol. The OutboundRunTailer calls
dispatch methods without knowing whether the target is CardKit,
Discord message edits, or something else.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class OpDispatcher(Protocol):
    async def dispatch_create(self, state: Any) -> bool: ...
    async def dispatch_stream(self, state: Any, text: str) -> bool: ...
    async def dispatch_patch(self, state: Any) -> bool: ...
    async def dispatch_finalize(self, state: Any) -> bool: ...
    async def emergency_text(self, text: str) -> None: ...
    async def aclose(self) -> None: ...
