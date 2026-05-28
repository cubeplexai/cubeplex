from cubebox.models.public_id import PREFIX_SANDBOX_POLICY
from cubebox.models.sandbox_policy import SandboxPolicy


def test_prefix_value() -> None:
    assert PREFIX_SANDBOX_POLICY == "sbxp"


def test_policy_autofills_prefixed_id() -> None:
    p = SandboxPolicy(org_id="org-1", default_image="ubuntu:22.04")
    assert p.id.startswith("sbxp-")
    assert p.org_id == "org-1"
    assert p.default_image == "ubuntu:22.04"
    # v1 only writes the org-default row (scope_workspace_id=NULL).
    assert p.scope_workspace_id is None
    assert p.network_rules is None
    assert p.command_rules is None


def test_policy_is_not_workspace_scoped_via_mixin() -> None:
    # Org-only table: must NOT carry a REQUIRED workspace_id column from
    # OrgScopedMixin. scope_workspace_id is a separate nullable column reserved
    # for v2 overrides; it is NOT the OrgScopedMixin's workspace_id.
    assert "workspace_id" not in SandboxPolicy.model_fields
    assert "scope_workspace_id" in SandboxPolicy.model_fields
