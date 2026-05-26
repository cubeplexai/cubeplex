from cubebox.models import SandboxEnvVar


def test_public_id_prefix():
    row = SandboxEnvVar(
        org_id="org-1",
        env_name="GITHUB_TOKEN",
        is_secret=True,
        scope="org",
        hosts=["api.github.com"],
        credential_id="cred-1",
    )
    assert row.id.startswith("senv-")


def test_plain_entry_shape():
    row = SandboxEnvVar(
        org_id="org-1",
        env_name="LOG_LEVEL",
        is_secret=False,
        scope="org",
        plain_value="debug",
    )
    assert row.is_secret is False
    assert row.plain_value == "debug"
    assert row.hosts is None
