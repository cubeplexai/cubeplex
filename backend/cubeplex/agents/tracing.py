"""Build the process-level cubepi Tracer from cubeplex config.

Tracing is opt-in via the ``tracing:`` config block. When disabled — or when
construction fails for any reason — this returns ``None`` and runs proceed
untraced. A tracing fault must never break the app, so every failure path
returns ``None``.

The Tracer is built once at app startup and reused across runs (each run
attaches/detaches via :func:`cubepi.tracing.trace`); it is shut down once at
app shutdown.

Two exporters can run in parallel: the JSONL file exporter (always on when
tracing is enabled) and an optional OTLP HTTP exporter that ships spans to an
external collector / Tempo. Set ``tracing.otlp.endpoint`` to the full traces
URL — e.g. ``http://localhost:4318/v1/traces`` — to enable the OTLP path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

from cubeplex.config import config

if TYPE_CHECKING:
    from cubepi.tracing import Tracer
    from opentelemetry.sdk.trace.export import SpanExporter


def _build_otlp_exporter() -> SpanExporter | None:
    """Build an OTLP HTTP span exporter when ``tracing.otlp.endpoint`` is set."""
    endpoint = config.get("tracing.otlp.endpoint", None)
    if not endpoint:
        return None
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    except ImportError as exc:
        logger.warning("OTLP exporter requested but package missing; skipping: {}", exc)
        return None

    raw_headers = config.get("tracing.otlp.headers", None)
    headers: dict[str, str] | None = None
    if isinstance(raw_headers, dict):
        headers = {str(k): str(v) for k, v in raw_headers.items()}

    kwargs: dict[str, Any] = {"endpoint": str(endpoint)}
    if headers:
        kwargs["headers"] = headers
    timeout = config.get("tracing.otlp.timeout_seconds", None)
    if timeout is not None:
        kwargs["timeout"] = int(timeout)
    return OTLPSpanExporter(**kwargs)


def build_tracer() -> Tracer | None:
    """Return a configured cubepi Tracer, or ``None`` when tracing is disabled.

    Reads ``tracing.enabled`` / ``tracing.directory`` / ``tracing.record_content``
    and (optionally) ``tracing.otlp.*`` from config. Import or construction
    failures are logged and swallowed.
    """
    try:
        if not config.get("tracing.enabled", False):
            return None

        from cubepi.tracing import JsonlSpanExporter, Tracer

        directory = config.get("tracing.directory", "./cubepi-traces")
        record_content = bool(config.get("tracing.record_content", False))

        exporters: list[Any] = [JsonlSpanExporter(directory=directory)]
        otlp = _build_otlp_exporter()
        if otlp is not None:
            exporters.append(otlp)
            logger.info(
                "Tracing OTLP exporter enabled (endpoint={})",
                config.get("tracing.otlp.endpoint"),
            )

        return Tracer(
            service_name="cubeplex",
            deployment_environment=str(config.get("env", "development")),
            agent_name="cubeplex-agent",
            exporters=exporters,
            record_content=record_content,
        )
    except Exception as exc:
        logger.warning("Tracing unavailable, continuing untraced: {}", exc)
        return None
