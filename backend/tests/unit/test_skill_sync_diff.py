"""Unit tests for compute_skill_sync_diff."""

from dataclasses import dataclass

from cubeplex.skills.sync_diff import compute_skill_sync_diff


@dataclass
class _FakeResolved:
    name: str
    version: str
    skill_version_id: str
    content_hash: str
    storage_prefix: str = "skills/_global/x/1.0.0/"


def _manifest(skills: dict[str, dict]) -> dict:
    return {"schema_version": 1, "skills": skills}


def test_empty_manifest_empty_desired_no_op():
    d = compute_skill_sync_diff(_manifest({}), [])
    assert d.is_empty()


def test_empty_manifest_with_desired_pushes_all():
    desired = [_FakeResolved("docx", "1.0.0", "skv_a", "sha256:aaa")]
    d = compute_skill_sync_diff(_manifest({}), desired)
    assert len(d.to_push) == 1
    assert d.to_push[0].name == "docx"
    assert d.to_remove == []
    assert d.to_keep == []
    assert not d.is_empty()


def test_manifest_matches_desired_keep_only():
    desired = [_FakeResolved("docx", "1.0.0", "skv_a", "sha256:aaa")]
    m = _manifest(
        {"docx": {"skill_version_id": "skv_a", "version": "1.0.0", "content_hash": "sha256:aaa"}}
    )
    d = compute_skill_sync_diff(m, desired)
    assert d.is_empty()
    assert d.to_keep == ["docx"]


def test_version_differs_pushes():
    desired = [_FakeResolved("docx", "1.1.0", "skv_b", "sha256:bbb")]
    m = _manifest(
        {"docx": {"skill_version_id": "skv_a", "version": "1.0.0", "content_hash": "sha256:aaa"}}
    )
    d = compute_skill_sync_diff(m, desired)
    assert [s.name for s in d.to_push] == ["docx"]
    assert d.to_remove == []  # same name, overwrite


def test_hash_differs_same_version_pushes():
    desired = [_FakeResolved("docx", "1.0.0", "skv_a", "sha256:NEW")]
    m = _manifest(
        {"docx": {"skill_version_id": "skv_a", "version": "1.0.0", "content_hash": "sha256:OLD"}}
    )
    d = compute_skill_sync_diff(m, desired)
    assert [s.name for s in d.to_push] == ["docx"]


def test_missing_in_desired_goes_to_remove():
    desired: list[_FakeResolved] = []
    m = _manifest(
        {"docx": {"skill_version_id": "skv_a", "version": "1.0.0", "content_hash": "sha256:aaa"}}
    )
    d = compute_skill_sync_diff(m, desired)
    assert d.to_push == []
    assert d.to_remove == ["docx"]


def test_colon_name_normalised_for_diff_key():
    # `<org>:<skill>` should be hashed as `<org>__<skill>` in manifest
    desired = [_FakeResolved("acme:my-skill", "1.0.0", "skv_a", "sha256:aaa")]
    m = _manifest(
        {
            "acme__my-skill": {
                "skill_version_id": "skv_a",
                "version": "1.0.0",
                "content_hash": "sha256:aaa",
            }
        }
    )
    d = compute_skill_sync_diff(m, desired)
    assert d.is_empty()


def test_mixed_case():
    desired = [
        _FakeResolved("a", "1.0.0", "skv_a", "sha256:aa"),  # keep
        _FakeResolved("b", "2.0.0", "skv_bb", "sha256:bb_new"),  # push (version differs)
        _FakeResolved("c", "1.0.0", "skv_c", "sha256:cc"),  # push (new)
    ]
    m = _manifest(
        {
            "a": {"skill_version_id": "skv_a", "version": "1.0.0", "content_hash": "sha256:aa"},
            "b": {"skill_version_id": "skv_b", "version": "1.0.0", "content_hash": "sha256:bb_old"},
            "d": {"skill_version_id": "skv_d", "version": "1.0.0", "content_hash": "sha256:dd"},
        }
    )
    d = compute_skill_sync_diff(m, desired)
    assert sorted(s.name for s in d.to_push) == ["b", "c"]
    assert d.to_remove == ["d"]
    assert d.to_keep == ["a"]


def test_legacy_empty_hash_no_perpetual_repush():
    """Legacy SkillVersion rows with content_hash == "" must NOT trigger
    re-push every sync. Once pushed (manifest stores ""), subsequent syncs
    must hit hot path. Otherwise backfill-less deployments churn forever."""
    desired = [_FakeResolved("docx", "1.0.0", "skv_a", "")]
    m = _manifest({"docx": {"skill_version_id": "skv_a", "version": "1.0.0", "content_hash": ""}})
    d = compute_skill_sync_diff(m, desired)
    # version matches + desired has no hash to verify against → hot path
    assert d.is_empty()


def test_legacy_empty_hash_pushes_on_cold_start():
    """First sync after deploy: manifest absent → push even if desired hash
    is empty. Only the steady-state (manifest matches) should hot-path."""
    desired = [_FakeResolved("docx", "1.0.0", "skv_a", "")]
    d = compute_skill_sync_diff(_manifest({}), desired)
    assert [s.name for s in d.to_push] == ["docx"]
