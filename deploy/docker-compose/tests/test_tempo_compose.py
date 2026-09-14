"""docker compose config contracts for the Tempo overlay."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

COMPOSE_DIR = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="docker not on PATH")

_DUMMY_ENV = {
    "POSTGRES_PASSWORD": "pg-password-generated",
    "REDIS_PASSWORD": "redis-password-generated",
    "RUSTFS_SECRET_KEY": "rustfs-secret-generated",
    "BACKEND_TAG": "v0.7.2",
    "FRONTEND_TAG": "v0.7.2",
}


def _compose_config(*files: str) -> dict[str, Any]:
    args = ["docker", "compose", "--project-directory", str(COMPOSE_DIR)]
    for name in files:
        args.extend(["-f", str(COMPOSE_DIR / name)])
    args.extend(["config", "--format", "json"])
    proc = subprocess.run(
        args,
        capture_output=True,
        text=True,
        env={**os.environ, **_DUMMY_ENV},
        check=False,
    )
    if proc.returncode != 0:
        pytest.fail(f"docker compose config failed:\n{proc.stderr or proc.stdout}")
    parsed = json.loads(proc.stdout)
    assert isinstance(parsed, dict)
    return parsed


def _service_env(service: dict[str, Any]) -> dict[str, str]:
    raw = service.get("environment") or {}
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    env: dict[str, str] = {}
    for item in raw:
        key, _, value = str(item).partition("=")
        env[key] = value
    return env


def _network_names(service: dict[str, Any]) -> set[str]:
    networks = service.get("networks") or {}
    if isinstance(networks, dict):
        return set(networks)
    return {str(name) for name in networks}


def test_overlay_adds_internal_tempo_and_wires_backend() -> None:
    cfg = _compose_config("compose.yaml", "compose.tempo.yaml")
    services = cfg["services"]
    assert "tempo" in services
    tempo = services["tempo"]
    assert _network_names(tempo) == {"tracing"}
    assert not tempo.get("ports")
    assert cfg.get("networks", {}).get("tracing", {}).get("internal") is True

    backend_env = _service_env(services["backend"])
    assert backend_env["CUBEPLEX_TRACING__OTLP__ENDPOINT"] == ("http://tempo:4318/v1/traces")
    assert backend_env["CUBEPLEX_TRACING__JSONL__ENABLED"] == "false"
    assert "tracing" in _network_names(services["backend"])
    assert "default" in _network_names(services["backend"])
    assert "tracing" not in _network_names(services["frontend"])
    assert "tracing" not in _network_names(services["opensandbox-server"])


def test_base_compose_has_no_tempo_or_tracing_env() -> None:
    cfg = _compose_config("compose.yaml")
    services = cfg["services"]
    assert "tempo" not in services
    backend_env = _service_env(services["backend"])
    tracing_keys = [k for k in backend_env if k.startswith("CUBEPLEX_TRACING__")]
    assert tracing_keys == []
