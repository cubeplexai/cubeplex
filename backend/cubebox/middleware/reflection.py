"""ReflectionMiddleware — end-of-run memory self-review."""

from __future__ import annotations

import time
from typing import Any

from cubepi.middleware.base import Middleware
from cubepi.providers.base import Message, TextContent, UserMessage

from cubebox.prompts.reflection import REFLECTION_PROMPT


class ReflectionMiddleware(Middleware):
    """Injects a memory-review prompt after every agent run completes.

    Uses the cubepi on_run_end hook so the reflection executes as an extra
    turn within the same run — same agent instance, same context, same tools.
    The injected UserMessage is tagged is_reflection=True so the frontend can
    render it differently in a future iteration.
    """

    async def on_run_end(self, ctx: Any, *, signal: Any = None) -> list[Message] | None:
        return [
            UserMessage(
                content=[TextContent(text=REFLECTION_PROMPT)],
                timestamp=time.time(),
                metadata={"is_reflection": True},
            )
        ]
