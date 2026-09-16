"""Contracts for the production Uvicorn entry point."""

from __future__ import annotations

import runpy
from pathlib import Path
from typing import Any

import uvicorn


def test_main_bounds_transport_shutdown_wait(monkeypatch: Any) -> None:
    """Long-lived SSE connections must not block lifespan shutdown forever."""
    captured: dict[str, Any] = {}

    def capture_run(app: str, **kwargs: Any) -> None:
        captured["app"] = app
        captured.update(kwargs)

    monkeypatch.setattr(uvicorn, "run", capture_run)
    backend_root = Path(__file__).resolve().parents[2]

    runpy.run_path(str(backend_root / "main.py"), run_name="__main__")

    assert captured["app"] == "cubeplex.api.app:create_app"
    assert captured["timeout_graceful_shutdown"] == 10
