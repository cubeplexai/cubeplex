"""Build sandbox env + egress network policy + ref bindings from resolved env."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from opensandbox.models.sandboxes import NetworkPolicy, NetworkRule

from cubebox.sandbox_env.placeholder import hash_placeholder, mint_placeholder
from cubebox.services.sandbox_env import ResolvedEnv


# Regex host patterns ("/.../" ) cannot be expressed as an egress allow-list
# rule (FQDN/wildcard only). Plan 1 guarantees a secret with a regex host also
# carries an FQDN/wildcard companion, so we simply skip regex items here.
def _allowlist_targets(hosts: list[str]) -> list[str]:
    return [h for h in hosts if not (h.startswith("/") and h.endswith("/"))]


@dataclass
class InjectionResult:
    env: dict[str, str] = field(default_factory=dict)
    network_policy: NetworkPolicy = field(default_factory=NetworkPolicy)
    bindings: list[dict[str, Any]] = field(default_factory=list)


class SandboxEnvInjector:
    def __init__(self, *, exchange_host: str) -> None:
        self._exchange_host = exchange_host

    def build(self, resolved: list[ResolvedEnv]) -> InjectionResult:
        env: dict[str, str] = {}
        bindings: list[dict[str, Any]] = []
        targets: set[str] = {self._exchange_host}

        for r in resolved:
            if r.is_secret:
                assert r.hosts and r.credential_id  # Plan 1 value-shape guarantees
                placeholder = mint_placeholder()
                env[r.env_name] = placeholder
                bindings.append(
                    {
                        "ref_hash": hash_placeholder(placeholder),
                        "env_name": r.env_name,
                        "hosts": r.hosts,
                        "header_names": r.header_names,
                        "credential_id": r.credential_id,
                    }
                )
                targets.update(_allowlist_targets(r.hosts))
            else:
                assert r.plain_value is not None
                env[r.env_name] = r.plain_value

        policy = NetworkPolicy(
            defaultAction="deny",
            egress=[NetworkRule(action="allow", target=t) for t in sorted(targets)],
        )
        return InjectionResult(env=env, network_policy=policy, bindings=bindings)
