from cubebox.sandbox_env.injector import SandboxEnvInjector
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
    # one binding for the secret, keyed by hash of its placeholder
    assert len(result.bindings) == 1
    assert result.bindings[0]["env_name"] == "GITHUB_TOKEN"
    assert result.bindings[0]["hosts"] == ["api.github.com"]
    # allow-list contains the secret host plus the exchange host
    targets = {r.target for r in result.network_policy.egress}
    assert "api.github.com" in targets
    assert "egress-exchange.internal" in targets


def test_wildcard_host_maps_to_allowlist_rule():
    inj = SandboxEnvInjector(exchange_host="x.internal")
    resolved = [ResolvedEnv("T", True, ["*.example.com"], None, "c", None)]
    result = inj.build(resolved)
    assert "*.example.com" in {r.target for r in result.network_policy.egress}
