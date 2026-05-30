import pytest

from cubebox.services.sandbox_policy import (
    EffectivePolicy,
    SandboxPolicyResolver,
    SandboxPolicyService,
    SandboxPolicyValidationError,
)


class _FakeRepo:
    """In-memory stand-in for SandboxPolicyRepository (org-default row)."""

    def __init__(self) -> None:
        self.row: dict | None = None

    async def get(self):
        return self.row

    async def upsert(self, **fields):
        self.row = {"org_id": "org-1", "scope_workspace_id": None, **fields}
        return self.row


async def test_resolver_returns_defaults_when_no_row() -> None:
    eff = await SandboxPolicyResolver(_FakeRepo(), default_image="ubuntu:22.04").resolve()
    assert isinstance(eff, EffectivePolicy)
    assert eff.default_image == "ubuntu:22.04"
    assert eff.network_rules == []
    assert eff.command_rules == []


async def test_resolver_returns_row_values() -> None:
    repo = _FakeRepo()
    repo.row = {
        "org_id": "org-1",
        "scope_workspace_id": None,
        "default_image": "python:3.12",
        "network_rules": [{"action": "deny", "target": "evil.test"}],
        "command_rules": [{"action": "deny", "pattern": "rm *"}],
        "network_default_action": "allow",
    }
    eff = await SandboxPolicyResolver(repo, default_image="ubuntu:22.04").resolve()
    assert eff.default_image == "python:3.12"
    assert eff.command_rules == [{"action": "deny", "pattern": "rm *"}]
    assert eff.network_default_action == "allow"


async def test_service_rejects_empty_default_image() -> None:
    svc = SandboxPolicyService(_FakeRepo())
    with pytest.raises(SandboxPolicyValidationError):
        await svc.upsert(
            default_image="",
            network_rules=None,
            command_rules=None,
            network_default_action="allow",
        )


async def test_service_rejects_bad_command_action() -> None:
    svc = SandboxPolicyService(_FakeRepo())
    with pytest.raises(SandboxPolicyValidationError):
        await svc.upsert(
            default_image="ubuntu:22.04",
            network_rules=None,
            command_rules=[{"action": "nuke", "pattern": "rm *"}],
            network_default_action="allow",
        )


async def test_service_rejects_bad_network_target() -> None:
    svc = SandboxPolicyService(_FakeRepo())
    with pytest.raises(SandboxPolicyValidationError):
        await svc.upsert(
            default_image="ubuntu:22.04",
            network_rules=[{"action": "allow", "target": "*"}],
            command_rules=None,
            network_default_action="allow",
        )


async def test_service_rejects_regex_network_target() -> None:
    """Regression for codex P1 r3317630103: the credential vault accepts
    anchored regex targets, but OpenSandbox network rules only honour
    FQDN/wildcard. A regex slips past validate_host_pattern but would not
    actually enforce the intended egress rule — reject at write time."""
    svc = SandboxPolicyService(_FakeRepo())
    with pytest.raises(SandboxPolicyValidationError, match="regex"):
        await svc.upsert(
            default_image="ubuntu:22.04",
            network_rules=[{"action": "deny", "target": r"/^api\.github\.com$/"}],
            command_rules=None,
            network_default_action="allow",
        )
    # Sanity: an FQDN and a wildcard still go through.
    await svc.upsert(
        default_image="ubuntu:22.04",
        network_rules=[
            {"action": "deny", "target": "api.github.com"},
            {"action": "allow", "target": "*.pypi.org"},
        ],
        command_rules=None,
        network_default_action="allow",
    )


async def test_resolver_defaults_action_to_deny_when_no_row() -> None:
    eff = await SandboxPolicyResolver(_FakeRepo(), default_image="ubuntu:22.04").resolve()
    assert eff.network_default_action == "deny"


async def test_resolver_returns_row_default_action() -> None:
    repo = _FakeRepo()
    repo.row = {
        "org_id": "org-1",
        "scope_workspace_id": None,
        "default_image": "python:3.12",
        "network_rules": None,
        "command_rules": None,
        "network_default_action": "deny",
    }
    eff = await SandboxPolicyResolver(repo, default_image="ubuntu:22.04").resolve()
    assert eff.network_default_action == "deny"


async def test_service_rejects_bad_default_action() -> None:
    svc = SandboxPolicyService(_FakeRepo())
    with pytest.raises(SandboxPolicyValidationError, match="default action"):
        await svc.upsert(
            default_image="ubuntu:22.04",
            network_rules=None,
            command_rules=None,
            network_default_action="maybe",
        )


async def test_service_rejects_contradictory_network_rules() -> None:
    # Same host, opposite actions — canonicalized (case + trailing dot).
    svc = SandboxPolicyService(_FakeRepo())
    with pytest.raises(SandboxPolicyValidationError, match="contradict"):
        await svc.upsert(
            default_image="ubuntu:22.04",
            network_rules=[
                {"action": "allow", "target": "API.GITHUB.COM"},
                {"action": "deny", "target": "api.github.com."},
            ],
            command_rules=None,
            network_default_action="allow",
        )


async def test_service_allows_duplicate_same_action() -> None:
    # Same host + same action is a harmless duplicate, not a contradiction.
    svc = SandboxPolicyService(_FakeRepo())
    await svc.upsert(
        default_image="ubuntu:22.04",
        network_rules=[
            {"action": "deny", "target": "api.github.com"},
            {"action": "deny", "target": "api.github.com"},
        ],
        command_rules=None,
        network_default_action="allow",
    )


async def test_upsert_normalizes_network_targets() -> None:
    repo = _FakeRepo()
    svc = SandboxPolicyService(repo)
    await svc.upsert(
        default_image="ubuntu:22.04",
        network_rules=[{"action": "deny", "target": "API.GitHub.com."}],
        command_rules=None,
        network_default_action="allow",
    )
    assert repo.row["network_rules"] == [{"action": "deny", "target": "api.github.com"}]
