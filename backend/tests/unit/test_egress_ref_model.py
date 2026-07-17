from cubeplex.models import EgressRef


def test_prefix_and_fields():
    ref = EgressRef(
        ref_hash="a" * 64,
        sandbox_id="sbx-1",
        org_id="org-1",
        workspace_id="ws-1",
        user_id="u-1",
        run_id="run-1",
        bindings=[
            {
                "env_name": "GITHUB_TOKEN",
                "hosts": ["api.github.com"],
                "header_names": None,
                "credential_id": "cred-1",
            }
        ],
    )
    assert ref.id.startswith("eref-")
    assert ref.status == "valid"
