from cubeplex.models.user_sandbox import UserSandbox


def test_new_lifecycle_columns_default():
    row = UserSandbox(
        user_id="u_1",
        sandbox_id="sbx_abc",
        image="ubuntu:22.04",
    )
    assert row.status == "running"
    assert row.provider == "opensandbox"
    assert row.paused_at is None
    assert row.last_resumed_at is None
    assert row.in_use_until is None
    assert row.paused_ttl_seconds == 24 * 60
