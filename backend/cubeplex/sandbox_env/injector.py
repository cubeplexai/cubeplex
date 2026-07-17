"""Build sandbox env + ref bindings from resolved env.

Network egress policy is intentionally NOT built here — see
``cubeplex.sandbox_policy.rules.build_network_policy``. The credential vault
only decides whether to substitute a placeholder for a host; whether the
sandbox can reach that host is the network policy's separate concern.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cubeplex.sandbox_env.placeholder import hash_placeholder, mint_placeholder
from cubeplex.services.sandbox_env import ResolvedEnv


@dataclass
class InjectionResult:
    env: dict[str, str] = field(default_factory=dict)
    bindings: list[dict[str, Any]] = field(default_factory=list)


class SandboxEnvInjector:
    def __init__(self, *, exchange_host: str) -> None:
        self._exchange_host = exchange_host

    def build(self, resolved: list[ResolvedEnv]) -> InjectionResult:
        env: dict[str, str] = {}
        bindings: list[dict[str, Any]] = []

        for r in resolved:
            if r.is_secret:
                if not (r.hosts and r.credential_id):
                    raise ValueError(
                        f"Secret env var {r.env_name!r} is missing hosts or credential_id"
                    )
                placeholder = mint_placeholder()
                env[r.env_name] = placeholder
                bindings.append(
                    {
                        "ref_hash": hash_placeholder(placeholder),
                        "env_name": r.env_name,
                        "env_var_id": r.id,
                        # hosts/header_names/credential_id are kept as a snapshot
                        # for backward-compat with old EgressRefs; the exchange
                        # service prefers the live DB lookup via env_var_id.
                        "hosts": r.hosts,
                        "header_names": r.header_names,
                        "credential_id": r.credential_id,
                    }
                )
            else:
                if r.value is None:
                    raise ValueError(
                        f"Env var {r.env_name!r} has no decrypted value — "
                        "call manager._decrypt_env_values() before build()"
                    )
                env[r.env_name] = r.value

        return InjectionResult(env=env, bindings=bindings)
