"""Build the process-level cubepi Tracer from cubebox config.

Tracing is opt-in via the ``tracing:`` config block. When disabled — or when
construction fails for any reason — this returns ``None`` and runs proceed
untraced. A tracing fault must never break the app, so every failure path
returns ``None``.

The Tracer is built once at app startup and reused across runs (each run
attaches/detaches via :func:`cubepi.tracing.trace`); it is shut down once at
app shutdown.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from cubebox.config import config

if TYPE_CHECKING:
    from cubepi.tracing import Tracer


def build_tracer() -> Tracer | None:
    """Return a configured cubepi Tracer, or ``None`` when tracing is disabled.

    Reads ``tracing.enabled`` / ``tracing.directory`` / ``tracing.record_content``
    from config. Import or construction failures are logged and swallowed.
    """
    try:
        if not config.get("tracing.enabled", False):
            return None

        from cubepi.tracing import JsonlSpanExporter, Tracer

        directory = config.get("tracing.directory", "./cubepi-traces")
        record_content = bool(config.get("tracing.record_content", False))
        return Tracer(
            service_name="cubebox",
            deployment_environment=str(config.get("env", "development")),
            agent_name="cubebox-agent",
            exporters=[JsonlSpanExporter(directory=directory)],
            record_content=record_content,
        )
    except Exception as exc:
        logger.warning("Tracing unavailable, continuing untraced: {}", exc)
        return None
