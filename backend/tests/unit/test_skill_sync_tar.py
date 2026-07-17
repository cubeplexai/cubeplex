"""Unit tests for tar packing + shell command building."""

import io
import shlex
import tarfile

from cubeplex.skills.sync_tar import build_extract_and_remove_cmd, build_tarball


def test_tar_roundtrip_preserves_content() -> None:
    files = [("docx/1.0.0/SKILL.md", b"# body"), ("docx/1.0.0/run.sh", b"echo 1")]
    blob = build_tarball(files)
    extracted = {}
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
        for member in tf.getmembers():
            f = tf.extractfile(member)
            assert f is not None
            extracted[member.name] = f.read()
    assert extracted == dict(files)


def test_tar_empty_input() -> None:
    blob = build_tarball([])
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
        assert tf.getmembers() == []


def test_tar_unicode_paths() -> None:
    files = [("中文/SKILL.md", b"body")]
    blob = build_tarball(files)
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
        names = [m.name for m in tf.getmembers()]
    assert names == ["中文/SKILL.md"]


def test_tar_no_leading_slash() -> None:
    files = [("/leading/slash", b"x")]
    blob = build_tarball(files)
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
        names = [m.name for m in tf.getmembers()]
    # tarfile strips leading slash by default; assert no surprises
    assert all(not n.startswith("/") for n in names)


def test_cmd_push_only_no_repush_no_remove() -> None:
    cmd = build_extract_and_remove_cmd(
        skills_root="/workspace/.skills",
        has_push=True,
        to_repush_names=[],
        to_remove=[],
    )
    assert "mkdir -p " + shlex.quote("/workspace/.skills") in cmd
    assert "tar -xzf /tmp/skills_delta.tgz -C " + shlex.quote("/workspace/.skills") in cmd
    assert "rm -f /tmp/skills_delta.tgz" in cmd
    assert "rm -rf " + shlex.quote("/workspace/.skills/") not in cmd


def test_cmd_push_with_repush_wipes_old_version_dirs() -> None:
    """Bump version case: when pushing skill X, wipe /workspace/.skills/X/
    BEFORE extract so old version dirs vanish (F9)."""
    cmd = build_extract_and_remove_cmd(
        skills_root="/workspace/.skills",
        has_push=True,
        to_repush_names=["docx"],
        to_remove=[],
    )
    # rm -rf must come BEFORE tar -xzf, otherwise we'd wipe what we just extracted
    rm_idx = cmd.index("rm -rf " + shlex.quote("/workspace/.skills/docx"))
    tar_idx = cmd.index("tar -xzf")
    assert rm_idx < tar_idx


def test_cmd_remove_only() -> None:
    cmd = build_extract_and_remove_cmd(
        skills_root="/workspace/.skills",
        has_push=False,
        to_repush_names=[],
        to_remove=["docx", "ppt"],
    )
    assert "mkdir -p" not in cmd
    assert "rm -rf " + shlex.quote("/workspace/.skills/docx") in cmd
    assert "rm -rf " + shlex.quote("/workspace/.skills/ppt") in cmd


def test_cmd_push_and_remove() -> None:
    cmd = build_extract_and_remove_cmd(
        skills_root="/workspace/.skills",
        has_push=True,
        to_repush_names=[],
        to_remove=["docx"],
    )
    # Whole command is && chained.
    assert " && " in cmd
    # Extract must come BEFORE the to_remove rm
    tar_idx = cmd.index("tar -xzf")
    rm_idx = cmd.index("rm -rf " + shlex.quote("/workspace/.skills/docx"))
    assert tar_idx < rm_idx
    # No `;` — only `&&` chaining.
    assert ";" not in cmd


def test_cmd_nothing_returns_empty_string() -> None:
    cmd = build_extract_and_remove_cmd(
        skills_root="/workspace/.skills",
        has_push=False,
        to_repush_names=[],
        to_remove=[],
    )
    assert cmd == ""


def test_cmd_handles_special_chars_in_skill_name() -> None:
    cmd = build_extract_and_remove_cmd(
        skills_root="/workspace/.skills",
        has_push=False,
        to_repush_names=[],
        to_remove=["a b'c"],
    )
    # shlex.quote should escape; raw single quote must not appear inside the quoted name
    assert "a b'c" not in cmd.split("rm -rf ", 1)[1].split(" ")[0]
