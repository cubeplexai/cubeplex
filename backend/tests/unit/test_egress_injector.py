from cubebox.sandbox_env.injector import InjectionResult, SandboxEnvInjector
from cubebox.sandbox_env.placeholder import PLACEHOLDER_RE
from cubebox.services.sandbox_env import ResolvedEnv


def test_secret_becomes_placeholder_plain_passes_through():
    inj = SandboxEnvInjector(exchange_host="egress-exchange.internal")
    resolved = [
        ResolvedEnv("GITHUB_TOKEN", True, ["api.github.com"], None, "cred-1", None),
        ResolvedEnv("LOG_LEVEL", False, None, None, None, "info"),
    ]
    result = inj.build(resolved)
    assert PLACEHOLDER_RE.fullmatch(result.env["GITHUB_TOKEN"])
    assert result.env["LOG_LEVEL"] == "info"
    assert len(result.bindings) == 1
    assert result.bindings[0]["env_name"] == "GITHUB_TOKEN"
    assert result.bindings[0]["hosts"] == ["api.github.com"]


def test_vault_hosts_do_not_leak_into_a_network_policy():
    # Network reachability is independent of credential substitution: the
    # injector must not expose any network policy / allow-list.
    inj = SandboxEnvInjector(exchange_host="x.internal")
    result = inj.build([ResolvedEnv("T", True, ["*.example.com"], None, "c", None)])
    assert not hasattr(result, "network_policy")
    assert isinstance(result, InjectionResult)
    assert result.bindings[0]["hosts"] == ["*.example.com"]
