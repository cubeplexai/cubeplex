"""helm template contracts for the bundled Tempo resources."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

CHART_DIR = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not on PATH")

# Secrets that pass chart `required` / placeholder checks. Not real credentials.
_STUB_VALUES: dict[str, Any] = {
    "opensandbox": {"enabled": False},
    "backend": {
        "secrets": {
            "auth": {
                "jwt_secret": "a" * 64,
                "csrf_secret": "b" * 64,
                "vault_key": "c" * 44,
            },
            "sandbox": {
                "domain": "opensandbox.example.svc.cluster.local:80",
                "api_key": "sandbox-api-key-generated",
            },
            "llm": {
                "model_presets": {
                    "tiers": {
                        "pro": {
                            "enabled": True,
                            "primary": "openai/gpt-5.6-terra",
                            "fallbacks": [],
                        }
                    },
                    "default_preset": "pro",
                },
                "providers": {},
            },
        }
    },
    "postgres": {"auth": {"password": "pg-password-generated"}},
    "redis": {"auth": {"password": "redis-password-generated"}},
    "rustfs": {"auth": {"secretKey": "rustfs-secret-generated"}},
}


def _chart_for_template(tmp_path: Path) -> Path:
    """Copy the chart without the gitignored OpenSandbox subchart tarball."""
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


def _helm_template(tmp_path: Path, extra: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    chart = _chart_for_template(tmp_path)
    values = yaml.dump({**_STUB_VALUES, **(extra or {})}, sort_keys=False)
    proc = subprocess.run(
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
    if proc.returncode != 0:
        pytest.fail(f"helm template failed:\n{proc.stderr or proc.stdout}")
    return [doc for doc in yaml.safe_load_all(proc.stdout) if doc]


def _by_kind(docs: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [d for d in docs if d.get("kind") == kind]


def _component(doc: dict[str, Any]) -> str:
    labels = (doc.get("metadata") or {}).get("labels") or {}
    return str(labels.get("app.kubernetes.io/component", ""))


def _backend_config(docs: list[dict[str, Any]]) -> dict[str, Any]:
    cms = [
        d
        for d in _by_kind(docs, "ConfigMap")
        if (d.get("metadata") or {}).get("name", "").endswith("-backend-config")
    ]
    assert cms, "backend ConfigMap missing"
    raw = cms[0]["data"]["config.production.local.yaml"]
    parsed = yaml.safe_load(raw)
    assert isinstance(parsed, dict)
    production = parsed.get("production")
    assert isinstance(production, dict)
    return production


def test_default_emits_clusterip_tempo_and_wires_backend(tmp_path: Path) -> None:
    docs = _helm_template(tmp_path)
    tempo_ss = [
        d
        for d in _by_kind(docs, "StatefulSet")
        if d.get("metadata", {}).get("name") == "cubeplex-tempo"
    ]
    tempo_svc = [
        d
        for d in _by_kind(docs, "Service")
        if d.get("metadata", {}).get("name") == "cubeplex-tempo"
    ]
    assert len(tempo_ss) == 1
    assert len(tempo_svc) == 1
    assert tempo_svc[0]["spec"]["type"] == "ClusterIP"
    ports = {p["port"] for p in tempo_svc[0]["spec"]["ports"]}
    assert {3200, 4318}.issubset(ports)

    ingress_yaml = yaml.dump(_by_kind(docs, "Ingress"))
    assert "tempo" not in ingress_yaml.lower()

    tracing = _backend_config(docs)["tracing"]
    assert tracing["otlp"]["endpoint"] == "http://cubeplex-tempo:4318/v1/traces"
    assert tracing["tempo"]["query_endpoint"] == "http://cubeplex-tempo:3200"
    assert tracing["record_content"] is False
    assert tracing["jsonl"]["enabled"] is False

    policies = [d for d in _by_kind(docs, "NetworkPolicy") if _component(d) == "tempo"]
    assert policies, "Tempo NetworkPolicy missing"
    from_components: set[str] = set()
    for rule in policies[0]["spec"].get("ingress") or []:
        for src in rule.get("from") or []:
            labels = (src.get("podSelector") or {}).get("matchLabels") or {}
            from_components.add(str(labels.get("app.kubernetes.io/component", "")))
    assert from_components == {"backend"}


def test_disabled_emits_no_tempo_and_no_injected_tracing(tmp_path: Path) -> None:
    docs = _helm_template(tmp_path, {"tempo": {"enabled": False}})
    tempo_objs = [d for d in docs if _component(d) == "tempo"]
    assert tempo_objs == []
    production = _backend_config(docs)
    assert "tracing" not in production


def test_enabled_replaces_configoverrides_tracing(tmp_path: Path) -> None:
    docs = _helm_template(
        tmp_path,
        {
            "backend": {
                **_STUB_VALUES["backend"],
                "configOverrides": {
                    "tracing": {
                        "tempo": {"query_endpoint": "http://should-be-stripped:3200"},
                    }
                },
            },
        },
    )
    tracing = _backend_config(docs)["tracing"]
    assert tracing["tempo"]["query_endpoint"] == "http://cubeplex-tempo:3200"


def test_disabled_preserves_byo_tracing_override(tmp_path: Path) -> None:
    docs = _helm_template(
        tmp_path,
        {
            "tempo": {"enabled": False},
            "backend": {
                **_STUB_VALUES["backend"],
                "configOverrides": {
                    "tracing": {
                        "enabled": True,
                        "tempo": {"query_endpoint": "http://external:3200"},
                    }
                },
            },
        },
    )
    tracing = _backend_config(docs)["tracing"]
    assert tracing["tempo"]["query_endpoint"] == "http://external:3200"
