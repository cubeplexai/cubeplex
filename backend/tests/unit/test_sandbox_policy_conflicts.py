from cubeplex.services.sandbox_policy_conflicts import credential_conflict_warnings


class _Cred:
    def __init__(self, id: str, env_name: str, hosts: list[str]) -> None:
        self.id = id
        self.env_name = env_name
        self.hosts = hosts


def test_warning_fires_for_case_and_trailing_dot_variant() -> None:
    creds = [_Cred("cred-1", "GH_TOKEN", ["api.github.com"])]
    rules = [{"action": "deny", "target": "API.GitHub.com."}]
    warnings = credential_conflict_warnings(rules, creds)
    assert len(warnings) == 1
    assert "cred-1" in warnings[0]


def test_no_warning_when_hosts_differ() -> None:
    creds = [_Cred("cred-1", "GH_TOKEN", ["api.github.com"])]
    rules = [{"action": "deny", "target": "api.gitlab.com"}]
    assert credential_conflict_warnings(rules, creds) == []


def test_wildcard_deny_still_warns_on_subdomain_cred_host() -> None:
    creds = [_Cred("cred-1", "GH_TOKEN", ["api.github.com"])]
    rules = [{"action": "deny", "target": "*.GITHUB.com"}]
    warnings = credential_conflict_warnings(rules, creds)
    assert len(warnings) == 1
