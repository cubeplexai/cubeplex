"""helm template contracts for multi-provider sandbox configuration."""

from __future__ import annotations

import copy
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

CHART_DIR = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not on PATH")

# Stub secrets satisfying chart required/placeholder validation. Not real credentials.
_BASE_VALUES: dict[str, Any] = {
    "opensandbox": {"enabled": False},
    "backend": {
        "secrets": {
            "auth": {
                "jwt_secret": "a" * 64,
                "csrf_secret": "b" * 64,
                "vault_key": "c" * 44,
            },
            "llm": {"providers": {}},
        }
    },
    "postgres": {"auth": {"password": "pg-password-generated"}},
    "redis": {"auth": {"password": "redis-password-generated"}},
    "rustfs": {"auth": {"secretKey": "rustfs-secret-generated"}},
}


def _chart_for_template(tmp_path: Path) -> Path:
    dest = tmp_path / "cubeplex"
    dest.mkdir()
    shutil.copy(CHART_DIR / "Chart.yaml", dest / "Chart.yaml")
    shutil.copy(CHART_DIR / "values.yaml", dest / "values.yaml")
    shutil.copytree(CHART_DIR / "templates", dest / "templates")
    files_dir = CHART_DIR / "files"
    if files_dir.is_dir():
        shutil.copytree(files_dir, dest / "files")
    chart = yaml.safe_load((dest / "Chart.yaml").read_text())
    chart.pop("dependencies", None)
    (dest / "Chart.yaml").write_text(yaml.safe_dump(chart, sort_keys=False))
    return dest


def _deep_merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, val in extra.items():
        if isinstance(val, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], val)
        else:
            merged[key] = copy.deepcopy(val)
    return merged


def _run_template(tmp_path: Path, overrides: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    chart = _chart_for_template(tmp_path)
    values = yaml.safe_dump(_deep_merge(_BASE_VALUES, overrides), sort_keys=False)
    return subprocess.run(
        [
            "helm",
            "template",
            "cubeplex",
            str(chart),
            "-f",
            str(chart / "values.yaml"),
            "-f",
            "-",
        ],
        input=values,
        capture_output=True,
        text=True,
        check=False,
    )


def _render_docs(tmp_path: Path, overrides: dict[str, Any]) -> list[dict[str, Any]]:
    proc = _run_template(tmp_path, overrides)
    if proc.returncode != 0:
        pytest.fail(f"helm template failed:\n{proc.stderr or proc.stdout}")
    return [doc for doc in yaml.safe_load_all(proc.stdout) if doc]


def _backend_config(docs: list[dict[str, Any]]) -> dict[str, Any]:
    cms = [
        d
        for d in docs
        if d.get("kind") == "ConfigMap"
        and (d.get("metadata") or {}).get("name", "").endswith("-backend-config")
    ]
    assert cms, "backend ConfigMap missing"
    parsed = yaml.safe_load(cms[0]["data"]["config.production.local.yaml"])
    return dict(parsed["production"])


def _backend_secret(docs: list[dict[str, Any]]) -> dict[str, Any]:
    secrets = [
        d
        for d in docs
        if d.get("kind") == "Secret"
        and (d.get("metadata") or {}).get("name", "").endswith("-backend-secrets")
    ]
    assert secrets, "backend Secret missing"
    parsed = yaml.safe_load(secrets[0]["stringData"]["config.production.secrets.yaml"])
    return dict(parsed["production"])


def test_default_opensandbox_preserves_existing_contract(tmp_path: Path) -> None:
    docs = _render_docs(
        tmp_path,
        {
            "backend": {
                "secrets": {
                    "sandbox": {
                        "domain": "opensandbox.example.svc.cluster.local:80",
                        "api_key": "opensandbox-key",
                    }
                }
            }
        },
    )
    sandbox_cfg = _backend_config(docs)["sandbox"]
    assert sandbox_cfg["enabled"] is True
    assert sandbox_cfg["provider"] == "opensandbox"
    assert "cubepod" not in sandbox_cfg

    sandbox_secret = _backend_secret(docs)["sandbox"]
    assert sandbox_secret["domain"] == "opensandbox.example.svc.cluster.local:80"
    assert sandbox_secret["api_key"] == "opensandbox-key"
    assert sandbox_secret["image"].startswith("ghcr.io/cubeplexai/cubeplex-sandbox:")
    assert "cubepod" not in sandbox_secret


def test_opensandbox_requires_domain_and_api_key(tmp_path: Path) -> None:
    proc = _run_template(
        tmp_path,
        {"backend": {"secrets": {"sandbox": {"api_key": "opensandbox-key"}}}},
    )
    assert proc.returncode != 0
    assert "backend.secrets.sandbox.domain" in (proc.stderr or proc.stdout)


def test_cubepod_minimal_does_not_require_opensandbox_or_api_key(tmp_path: Path) -> None:
    docs = _render_docs(
        tmp_path,
        {
            "backend": {
                "sandbox": {
                    "provider": "cubepod",
                    "cubepod": {"base_url": "http://cubepod-gateway:8080"},
                }
            }
        },
    )
    sandbox_cfg = _backend_config(docs)["sandbox"]
    assert sandbox_cfg["provider"] == "cubepod"
    assert sandbox_cfg["cubepod"] == {
        "base_url": "http://cubepod-gateway:8080",
        "default_template": "cubeplex-ubuntu24-desktop",
    }
    assert "sandbox" not in _backend_secret(docs)


def test_cubepod_full_renders_optional_api_key_warm_pool_and_template(tmp_path: Path) -> None:
    docs = _render_docs(
        tmp_path,
        {
            "backend": {
                "sandbox": {
                    "provider": "cubepod",
                    "cubepod": {
                        "base_url": "http://cubepod-gateway:8080",
                        "default_template": "custom-template",
                        "warm_pool": "desktop-warm-pool",
                    },
                },
                "secrets": {"sandbox": {"cubepod": {"api_key": "cubepod-token"}}},
            }
        },
    )
    sandbox_cfg = _backend_config(docs)["sandbox"]
    assert sandbox_cfg["cubepod"] == {
        "base_url": "http://cubepod-gateway:8080",
        "default_template": "custom-template",
        "warm_pool": "desktop-warm-pool",
    }
    assert _backend_secret(docs)["sandbox"] == {"cubepod": {"api_key": "cubepod-token"}}


def test_cubepod_requires_base_url_and_handles_null_subdict(tmp_path: Path) -> None:
    proc = _run_template(
        tmp_path,
        {"backend": {"sandbox": {"provider": "cubepod", "cubepod": None}}},
    )
    assert proc.returncode != 0
    assert "backend.sandbox.cubepod.base_url" in (proc.stderr or proc.stdout)


def test_local_provider_requires_no_external_sandbox_secrets(tmp_path: Path) -> None:
    docs = _render_docs(tmp_path, {"backend": {"sandbox": {"provider": "local"}}})
    sandbox_cfg = _backend_config(docs)["sandbox"]
    assert sandbox_cfg["provider"] == "local"
    assert "cubepod" not in sandbox_cfg
    assert "sandbox" not in _backend_secret(docs)
