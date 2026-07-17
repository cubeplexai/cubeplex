"""Unit tests for the process-level cubepi Tracer factory."""

from __future__ import annotations

import pytest

from cubeplex.agents import tracing as tracing_mod


def _fake_config(values: dict[str, object]):
    """Return a stub with a dynaconf-like .get(key, default)."""

    class _Stub:
        def get(self, key: str, default: object = None) -> object:
            return values.get(key, default)

    return _Stub()


def test_build_tracer_disabled_returns_none(monkeypatch):
    monkeypatch.setattr(tracing_mod, "config", _fake_config({"tracing.enabled": False}))
    assert tracing_mod.build_tracer() is None


def test_build_tracer_missing_key_defaults_disabled(monkeypatch):
    # No tracing.enabled key at all -> default False -> None.
    monkeypatch.setattr(tracing_mod, "config", _fake_config({}))
    assert tracing_mod.build_tracer() is None


@pytest.mark.asyncio
async def test_build_tracer_enabled_returns_tracer(monkeypatch, tmp_path):
    from cubepi.tracing import Tracer

    monkeypatch.setattr(
        tracing_mod,
        "config",
        _fake_config(
            {
                "tracing.enabled": True,
                "tracing.directory": str(tmp_path),
                "tracing.record_content": True,
                "env": "development",
            }
        ),
    )
    tracer = tracing_mod.build_tracer()
    assert isinstance(tracer, Tracer)
    # Without otlp.endpoint, only the JSONL processor is attached.
    assert len(tracer._processors) == 1
    # Clean up so the BatchSpanProcessor / atexit hook doesn't leak.
    await tracer.shutdown()


@pytest.mark.asyncio
async def test_build_tracer_attaches_otlp_when_endpoint_set(monkeypatch, tmp_path):
    from cubepi.tracing import Tracer

    monkeypatch.setattr(
        tracing_mod,
        "config",
        _fake_config(
            {
                "tracing.enabled": True,
                "tracing.directory": str(tmp_path),
                "tracing.record_content": False,
                "tracing.otlp.endpoint": "http://localhost:4318/v1/traces",
                "tracing.otlp.headers": {"Authorization": "Bearer x"},
                "env": "development",
            }
        ),
    )
    tracer = tracing_mod.build_tracer()
    assert isinstance(tracer, Tracer)
    # JSONL + OTLP → two BatchSpanProcessors.
    assert len(tracer._processors) == 2
    await tracer.shutdown()


def test_build_otlp_exporter_none_when_endpoint_unset(monkeypatch):
    monkeypatch.setattr(tracing_mod, "config", _fake_config({}))
    assert tracing_mod._build_otlp_exporter() is None
