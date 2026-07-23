"""Unit tests for agent-facing sandbox runtime config serialization.

Business contract: serializers never emit secret values, credential ids, proxy
credentials, or unknown keys — only whitelist fields for diagnosis.
"""

from __future__ import annotations

from typing import Any

import pytest

from cubeplex.services.sandbox_policy import EffectivePolicy
from cubeplex.services.sandbox_runtime_config import (
    FORBIDDEN_KEY_FRAGMENTS,
    GUIDANCE,
    POLICY_SOURCE,
    SANDBOX_NOTE,
    EnvInventoryItem,
    load_agent_view,
    serialize_command_rules,
    serialize_env_inventory,
    serialize_network_policy,
)


def _forbidden_paths(obj: Any, path: str = "$") -> list[str]:
    """Recursively collect paths whose keys look secret-bearing or unknown."""
    hits: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            key_l = str(key).lower()
            if any(frag in key_l for frag in FORBIDDEN_KEY_FRAGMENTS):
                hits.append(f"{path}.{key}")
            hits.extend(_forbidden_paths(value, f"{path}.{key}"))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            hits.extend(_forbidden_paths(item, f"{path}[{i}]"))
    return hits


def test_network_policy_whitelist_drops_extra_rule_keys() -> None:
    policy = EffectivePolicy(
        default_image="img",
        network_default_action="deny",
        network_rules=[
            {
                "action": "allow",
                "target": "pypi.org",
                "credential_id": "cred-leak",
                "secret": "should-not-appear",
                "value": "nope",
            }
        ],
        egress_proxy="http://proxy.example:8080",
    )
    out = serialize_network_policy(policy)
    assert out["default_action"] == "deny"
    assert out["rules"] == [{"action": "allow", "target": "pypi.org"}]
    assert out["egress_proxy"] == "set"
    assert out["policy_source"] == POLICY_SOURCE
    assert out["sandbox_note"] == SANDBOX_NOTE
    assert out["truncated"] is False
    assert _forbidden_paths(out) == []


def test_network_policy_egress_unset_when_none() -> None:
    policy = EffectivePolicy(default_image="img", egress_proxy=None)
    out = serialize_network_policy(policy)
    assert out["egress_proxy"] == "unset"


def test_network_policy_truncates_rules() -> None:
    rules = [{"action": "allow", "target": f"h{i}.example"} for i in range(150)]
    policy = EffectivePolicy(default_image="img", network_rules=rules)
    out = serialize_network_policy(policy, max_rules=100)
    assert len(out["rules"]) == 100
    assert out["truncated"] is True


def test_env_inventory_never_emits_values_or_credential_ids() -> None:
    items = [
        EnvInventoryItem(
            env_name="GITHUB_TOKEN",
            kind="secret",
            scope="user",
            status="valid",
            hosts=["api.github.com"],
            header_names=["Authorization"],
        ),
        EnvInventoryItem(
            env_name="LOG_LEVEL",
            kind="plain",
            scope="org",
            status="valid",
            hosts=None,
            header_names=None,
        ),
    ]
    # Poison: if serializer ever model_dump'd a richer object, these would leak.
    poisoned = items  # type: ignore[assignment]
    out, truncated = serialize_env_inventory(poisoned)
    assert truncated is False
    assert out == [
        {
            "env_name": "GITHUB_TOKEN",
            "kind": "secret",
            "scope": "user",
            "status": "valid",
            "hosts": ["api.github.com"],
            "header_names": ["Authorization"],
        },
        {
            "env_name": "LOG_LEVEL",
            "kind": "plain",
            "scope": "org",
            "status": "valid",
        },
    ]
    assert _forbidden_paths(out) == []
    blob = str(out)
    assert "cred-" not in blob
    assert "sk-" not in blob
    assert "super-secret" not in blob


def test_env_inventory_truncates() -> None:
    items = [
        EnvInventoryItem(
            env_name=f"E{i}",
            kind="plain",
            scope="org",
            status="valid",
            hosts=None,
            header_names=None,
        )
        for i in range(12)
    ]
    out, truncated = serialize_env_inventory(items, max_items=10)
    assert len(out) == 10
    assert truncated is True


def test_command_rules_whitelist() -> None:
    policy = EffectivePolicy(
        default_image="img",
        command_rules=[
            {"action": "deny", "pattern": "rm *", "extra": "nope", "value": "x"},
            {"action": "confirm", "pattern": "sudo *"},
        ],
    )
    out = serialize_command_rules(policy)
    assert out == [
        {"action": "deny", "pattern": "rm *"},
        {"action": "confirm", "pattern": "sudo *"},
    ]
    assert _forbidden_paths(out) == []


def test_command_rules_truncates() -> None:
    policy = EffectivePolicy(
        default_image="img",
        command_rules=[{"action": "deny", "pattern": f"p{i}"} for i in range(5)],
    )
    out = serialize_command_rules(policy, max_rules=3)
    assert len(out) == 3


@pytest.mark.asyncio
async def test_load_agent_view_uses_resolvers_not_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """load_agent_view must assemble via whitelist serializers only."""

    class _FakeSession:
        pass

    async def fake_policy_resolve(self: object) -> EffectivePolicy:
        return EffectivePolicy(
            default_image="img",
            network_default_action="deny",
            network_rules=[{"action": "allow", "target": "pypi.org", "value": "leak"}],
            command_rules=[{"action": "deny", "pattern": "rm *", "credential_id": "c"}],
            egress_proxy=None,
        )

    async def fake_inventory(
        session: object, *, org_id: str, workspace_id: str, user_id: str
    ) -> list[EnvInventoryItem]:
        del session, org_id, workspace_id, user_id
        return [
            EnvInventoryItem(
                env_name="API_KEY",
                kind="secret",
                scope="org",
                status="valid",
                hosts=["api.example.com"],
                header_names=None,
            )
        ]

    monkeypatch.setattr(
        "cubeplex.services.sandbox_runtime_config.SandboxPolicyResolver.resolve",
        fake_policy_resolve,
    )
    monkeypatch.setattr(
        "cubeplex.services.sandbox_runtime_config.resolve_env_inventory",
        fake_inventory,
    )

    view = await load_agent_view(
        _FakeSession(),  # type: ignore[arg-type]
        org_id="org1",
        workspace_id="ws1",
        user_id="u1",
        default_image="ubuntu:22.04",
    )
    assert view["guidance"] == GUIDANCE
    assert view["network"]["rules"] == [{"action": "allow", "target": "pypi.org"}]
    assert view["command_rules"] == [{"action": "deny", "pattern": "rm *"}]
    assert view["env"][0]["env_name"] == "API_KEY"
    assert "value" not in view["env"][0]
    assert _forbidden_paths(view) == []
