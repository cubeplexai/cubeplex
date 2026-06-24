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


async def test_resolver_passes_resource_fields_through() -> None:
    repo = _FakeRepo()
    repo.row = {
        "org_id": "org-1",
        "scope_workspace_id": None,
        "default_image": "python:3.12",
        "network_rules": None,
        "command_rules": None,
        "network_default_action": "deny",
        "resource_cpu": "500m",
        "resource_memory": "2Gi",
        "storage": "10Gi",
    }
    eff = await SandboxPolicyResolver(repo, default_image="ubuntu:22.04").resolve()
    assert eff.resource_cpu == "500m"
    assert eff.resource_memory == "2Gi"
    assert eff.storage == "10Gi"


async def test_resolver_resource_fields_none_when_no_row() -> None:
    eff = await SandboxPolicyResolver(_FakeRepo(), default_image="ubuntu:22.04").resolve()
    assert eff.resource_cpu is None
    assert eff.resource_memory is None
    assert eff.storage is None


async def test_upsert_persists_valid_resource_quantities() -> None:
    repo = _FakeRepo()
    svc = SandboxPolicyService(repo)
    # 500m is a valid CPU quantity (half a core); exponent notation is valid
    # for memory; binary suffix for storage.
    await svc.upsert(
        default_image="ubuntu:22.04",
        network_rules=None,
        command_rules=None,
        network_default_action="deny",
        resource_cpu="500m",
        resource_memory="1e9",
        storage="20Gi",
    )
    assert repo.row["resource_cpu"] == "500m"
    assert repo.row["resource_memory"] == "1e9"
    assert repo.row["storage"] == "20Gi"


async def test_upsert_treats_blank_resource_as_unset() -> None:
    repo = _FakeRepo()
    svc = SandboxPolicyService(repo)
    await svc.upsert(
        default_image="ubuntu:22.04",
        network_rules=None,
        command_rules=None,
        network_default_action="deny",
        resource_cpu="  ",
        resource_memory="",
        storage=None,
    )
    assert repo.row["resource_cpu"] is None
    assert repo.row["resource_memory"] is None
    assert repo.row["storage"] is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("resource_cpu", "lots"),
        ("resource_memory", "2 GB"),
        ("storage", "10gigs"),
        ("resource_cpu", "0"),
        ("resource_memory", "-1Gi"),
        # 'm' (milli) is CPU-only; k8s reads memory/storage '512m' as 0.512
        # bytes, so it must be rejected for byte-denominated fields.
        ("resource_memory", "512m"),
        ("storage", "10m"),
        # Over-length values must 400 here, not truncate at the 32-char column.
        ("resource_memory", "9" * 40 + "Gi"),
    ],
)
async def test_upsert_rejects_bad_resource_quantity(field: str, value: str) -> None:
    svc = SandboxPolicyService(_FakeRepo())
    with pytest.raises(SandboxPolicyValidationError, match=field):
        await svc.upsert(
            default_image="ubuntu:22.04",
            network_rules=None,
            command_rules=None,
            network_default_action="deny",
            **{field: value},
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
