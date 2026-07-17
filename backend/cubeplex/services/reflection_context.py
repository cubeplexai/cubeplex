"""ContextVar gate for tagging memory writes made during a reflection run.

Set inside ReflectionRunner around the reflection Agent's prompt; read by
the memory_save / memory_update tools to override source_type. Using a
ContextVar (not a tool argument) means the main agent cannot impersonate
reflection-sourced writes.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_active: ContextVar[bool] = ContextVar("reflection_source_active", default=False)


def reflection_source_active() -> bool:
    return _active.get()


@contextmanager
def set_reflection_source() -> Iterator[None]:
    token = _active.set(True)
    try:
        yield
    finally:
        _active.reset(token)
