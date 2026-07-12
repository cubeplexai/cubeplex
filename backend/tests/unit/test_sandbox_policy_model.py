from cubeplex.models.public_id import PREFIX_SANDBOX_POLICY
from cubeplex.models.sandbox_policy import SandboxPolicy


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


def test_uniqueness_is_enforced_with_null_safe_partial_indexes() -> None:
    # Postgres (and SQLite) treat NULL as distinct in unique indexes, so a
    # single UNIQUE(org_id, scope_workspace_id) would silently allow two
    # NULL-scope rows per org. The table splits the constraint into two
    # partial indexes — one for the org-default shape (scope_workspace_id
    # IS NULL), one for v2 per-workspace overrides (NOT NULL).
    index_names = {ix.name for ix in SandboxPolicy.__table__.indexes}
    assert "uq_sandbox_policy_org_default" in index_names
    assert "uq_sandbox_policy_org_workspace" in index_names
    # The old non-partial index must be gone — its presence would silently
    # re-introduce the NULL-distinct gap on Postgres.
    assert "uq_sandbox_policy_scope" not in index_names
