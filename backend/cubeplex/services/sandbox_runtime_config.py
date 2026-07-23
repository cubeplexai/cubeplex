"""Agent-facing sandbox runtime config: network policy + env inventory (no secrets).

Used by the ``sandbox_config`` tool. Always whitelist-serialize; never construct
``CredentialService`` or pass ``ResolvedEnv.value`` through.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.repositories.sandbox_env import SandboxEnvRepository
from cubeplex.repositories.sandbox_policy import SandboxPolicyRepository
from cubeplex.services.sandbox_policy import EffectivePolicy, SandboxPolicyResolver

# Match SandboxEnvResolver precedence (user > workspace > org).
_SCOPE_RANK = {"user": 3, "workspace": 2, "org": 1}

POLICY_SOURCE = "org_db_current"
SANDBOX_NOTE = "network rules apply at create; recreate sandbox after admin changes"
GUIDANCE = (
    "Call sandbox_config on network, auth, or missing-env failures. "
    "Do not invent credentials or print secret values. "
    "Prefer this tool over printenv for diagnosis. "
    "Point the user to admin/workspace sandbox settings for missing allow rules "
    "or env entries."
)
POLICY_DENY_NUDGE = "For network/env inventory call sandbox_config."

DEFAULT_MAX_RULES = 100
DEFAULT_MAX_ENV = 100

# Keys (or substrings) that must never appear in agent-facing JSON.
FORBIDDEN_KEY_FRAGMENTS: frozenset[str] = frozenset(
    {
        "value",
        "secret",
        "credential",
        "password",
        "token",
        "plaintext",
        "proxy_user",
        "proxy_pass",
        "authorization",
    }
)


@dataclass(frozen=True)
class EnvInventoryItem:
    """Non-secret metadata for one winning (injectable) env entry."""

    env_name: str
    kind: Literal["plain", "secret"]
    scope: str
    status: str
    hosts: list[str] | None
    header_names: list[str] | None


def serialize_network_policy(
    policy: EffectivePolicy,
    *,
    max_rules: int = DEFAULT_MAX_RULES,
) -> dict[str, Any]:
    raw_rules = list(policy.network_rules or [])
    truncated = len(raw_rules) > max_rules
    rules: list[dict[str, str]] = []
    for rule in raw_rules[:max_rules]:
        action = str(rule.get("action", "")).strip()
        target = str(rule.get("target", "")).strip()
        if not action or not target:
            continue
        rules.append({"action": action, "target": target})
    return {
        "default_action": policy.network_default_action,
        "rules": rules,
        "egress_proxy": "set" if policy.egress_proxy else "unset",
        "policy_source": POLICY_SOURCE,
        "sandbox_note": SANDBOX_NOTE,
        "truncated": truncated,
    }


def serialize_command_rules(
    policy: EffectivePolicy,
    *,
    max_rules: int = DEFAULT_MAX_RULES,
) -> list[dict[str, str]]:
    raw = list(policy.command_rules or [])
    out: list[dict[str, str]] = []
    for rule in raw[:max_rules]:
        action = str(rule.get("action", "")).strip()
        pattern = str(rule.get("pattern", "")).strip()
        if not action or not pattern:
            continue
        out.append({"action": action, "pattern": pattern})
    return out


def serialize_env_inventory(
    items: list[EnvInventoryItem],
    *,
    max_items: int = DEFAULT_MAX_ENV,
) -> tuple[list[dict[str, Any]], bool]:
    truncated = len(items) > max_items
    out: list[dict[str, Any]] = []
    for item in items[:max_items]:
        entry: dict[str, Any] = {
            "env_name": item.env_name,
            "kind": item.kind,
            "scope": item.scope,
            "status": item.status,
        }
        if item.kind == "secret":
            if item.hosts is not None:
                entry["hosts"] = list(item.hosts)
            if item.header_names is not None:
                entry["header_names"] = list(item.header_names)
        out.append(entry)
    return out, truncated


async def resolve_env_inventory(
    session: AsyncSession,
    *,
    org_id: str,
    workspace_id: str,
    user_id: str,
) -> list[EnvInventoryItem]:
    """Winning valid rows only (same inject set as SandboxEnvResolver)."""
    repo = SandboxEnvRepository(session, org_id=org_id)
    rows = await repo.list_for_resolution(workspace_id=workspace_id, user_id=user_id)
    best: dict[str, Any] = {}
    for row in rows:
        cur = best.get(row.env_name)
        if cur is None or _SCOPE_RANK[row.scope] > _SCOPE_RANK[cur.scope]:
            best[row.env_name] = row
    items: list[EnvInventoryItem] = []
    for r in best.values():
        items.append(
            EnvInventoryItem(
                env_name=r.env_name,
                kind="secret" if r.is_secret else "plain",
                scope=r.scope,
                status=getattr(r, "status", None) or "valid",
                hosts=list(r.hosts) if r.hosts is not None else None,
                header_names=list(r.header_names) if r.header_names is not None else None,
            )
        )
    items.sort(key=lambda i: i.env_name)
    return items


async def load_agent_view(
    session: AsyncSession,
    *,
    org_id: str,
    workspace_id: str,
    user_id: str,
    default_image: str,
    max_rules: int = DEFAULT_MAX_RULES,
    max_env: int = DEFAULT_MAX_ENV,
) -> dict[str, Any]:
    """Load and serialize effective policy + env inventory for the agent tool.

    Opens no credentials path. Caller owns the session lifecycle.
    """
    policy = await SandboxPolicyResolver(
        SandboxPolicyRepository(session, org_id=org_id),
        default_image=default_image,
    ).resolve()
    inventory = await resolve_env_inventory(
        session,
        org_id=org_id,
        workspace_id=workspace_id,
        user_id=user_id,
    )
    network = serialize_network_policy(policy, max_rules=max_rules)
    env_list, env_truncated = serialize_env_inventory(inventory, max_items=max_env)
    command_rules = serialize_command_rules(policy, max_rules=max_rules)
    cmd_truncated = len(policy.command_rules or []) > max_rules
    return {
        "network": network,
        "env": env_list,
        "command_rules": command_rules,
        "truncated": bool(network["truncated"] or env_truncated or cmd_truncated),
        "guidance": GUIDANCE,
    }
