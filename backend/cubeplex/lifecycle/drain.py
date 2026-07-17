"""Process-level drain state machine.

The state machine is read by request handlers (drain middleware, health
probes) and written by signal handlers + the FastAPI lifespan shutdown
hook. It does not own any async waiting — that lives in
``RunManager.drain()``.
"""

from __future__ import annotations

from typing import Literal

State = Literal["accepting", "draining"]


class DrainState:
    """Single-process drain flag.

    Transitions:
        accepting -> draining (via enter_draining; idempotent)

    The 'exiting' phase from the design doc is a property of the lifespan
    shutdown sequence, not a state we observe at runtime. Once
    ``RunManager.drain()`` returns, the process tears down.
    """

    __slots__ = ("_state",)

    def __init__(self) -> None:
        self._state: State = "accepting"

    def is_accepting(self) -> bool:
        return self._state == "accepting"

    def is_draining(self) -> bool:
        return self._state == "draining"

    def enter_draining(self) -> None:
        self._state = "draining"
