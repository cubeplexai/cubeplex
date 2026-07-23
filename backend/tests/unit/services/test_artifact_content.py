"""Unit tests for markdown eligibility and path helpers."""

from types import SimpleNamespace

from cubeplex.services.artifact_content import (
    is_markdown_eligible,
    markdown_filename,
    resolve_sandbox_write_path,
)


def _art(**kwargs: object) -> SimpleNamespace:
    defaults: dict[str, object] = {
        "artifact_type": "document",
        "path": "/workspace/docs/guide.md",
        "entry_file": None,
        "mime_type": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_markdown_filename_prefers_entry() -> None:
    assert markdown_filename(_art(path="/workspace/docs", entry_file="nested/README.md")) == (
        "README.md"
    )


def test_markdown_filename_rejects_escape() -> None:
    assert markdown_filename(_art(entry_file="../x.md")) is None
    assert markdown_filename(_art(entry_file="/etc/passwd")) is None


def test_is_markdown_eligible_by_extension() -> None:
    assert is_markdown_eligible(_art()) is True
    assert is_markdown_eligible(_art(path="/workspace/a.pdf")) is False


def test_is_markdown_eligible_by_mime() -> None:
    assert is_markdown_eligible(_art(path="/workspace/x", mime_type="text/markdown")) is True


def test_resolve_sandbox_write_path_file() -> None:
    path, reason = resolve_sandbox_write_path(_art())
    assert reason is None
    assert path == "/workspace/docs/guide.md"


def test_resolve_sandbox_write_path_entry() -> None:
    path, reason = resolve_sandbox_write_path(_art(path="/workspace/docs", entry_file="README.md"))
    assert reason is None
    assert path == "/workspace/docs/README.md"


def test_resolve_sandbox_write_path_escape() -> None:
    path, reason = resolve_sandbox_write_path(_art(entry_file="../secret.md"))
    assert path is None
    assert reason == "path_escape"
