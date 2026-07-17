import pytest

from cubeplex.sandbox_env.injector import InjectionResult, SandboxEnvInjector
from cubeplex.sandbox_env.placeholder import PLACEHOLDER_RE
from cubeplex.services.sandbox_env import ResolvedEnv


def test_secret_becomes_placeholder_env_value_passes_through():
    inj = SandboxEnvInjector(exchange_host="egress-exchange.internal")
    resolved = [
        ResolvedEnv("senv-1", "GITHUB_TOKEN", True, ["api.github.com"], None, "cred-1"),
        ResolvedEnv("senv-2", "LOG_LEVEL", False, None, None, None, value="info"),
    ]
    result = inj.build(resolved)
    assert PLACEHOLDER_RE.fullmatch(result.env["GITHUB_TOKEN"])
    assert result.env["LOG_LEVEL"] == "info"
    assert len(result.bindings) == 1
    assert result.bindings[0]["env_name"] == "GITHUB_TOKEN"


def test_env_value_missing_value_raises():
    inj = SandboxEnvInjector(exchange_host="x.internal")
    with pytest.raises(ValueError, match="no decrypted value"):
        inj.build([ResolvedEnv("senv-x", "MISSING", False, None, None, None)])


def test_vault_hosts_do_not_leak_into_a_network_policy():
    # Network reachability is independent of credential substitution: the
    # injector must not expose any network policy / allow-list.
    inj = SandboxEnvInjector(exchange_host="x.internal")
    result = inj.build([ResolvedEnv("senv-t", "T", True, ["*.example.com"], None, "c")])
    assert not hasattr(result, "network_policy")
    assert isinstance(result, InjectionResult)
    assert result.bindings[0]["hosts"] == ["*.example.com"]
