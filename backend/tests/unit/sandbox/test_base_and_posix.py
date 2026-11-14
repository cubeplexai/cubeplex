"""Unit tests for Unit 1: Sandbox data-plane extensions, capabilities, and PosixSandboxMixin."""

from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from cubeplex.sandbox.base import (
    ExecuteResult,
    FileEntry,
    Sandbox,
    SandboxCapabilities,
    SandboxCapabilityUnsupportedError,
    SandboxError,
    SandboxFileTooLargeError,
)
from cubeplex.sandbox.panel_token import verify_panel_token
from cubeplex.sandbox.posix import PosixSandboxMixin
from cubeplex.skills.sync_tar import SKILLS_DELTA_TGZ_PATH


class _StubBaseSandbox(Sandbox):
    def __init__(self, caps: SandboxCapabilities | None = None, exit_code: int = 0) -> None:
        self._caps = caps or SandboxCapabilities()
        self._exit_code = exit_code
        self.uploaded: list[tuple[str, bytes]] = []
        self.commands: list[tuple[str, bool]] = []

    @property
    def id(self) -> str:
        return "sbx_base"

    @property
    def workdir(self) -> str:
        return "/workspace"

    @property
    def provider_name(self) -> str:
        return "stub-base"

    @property
    def capabilities(self) -> SandboxCapabilities:
        return self._caps

    async def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
        envs: dict[str, str] | None = None,
        as_root: bool = False,
        on_chunk=None,
    ) -> ExecuteResult:
        del timeout, envs, on_chunk
        self.commands.append((command, as_root))
        return ExecuteResult(output="", exit_code=self._exit_code)

    async def upload(self, files: list[tuple[str, bytes]]) -> None:
        self.uploaded.extend(files)

    async def download(self, paths: list[str]) -> list[tuple[str, bytes]]:
        del paths
        return []

    async def close(self) -> None:
        return


class _StubPosixSandbox(PosixSandboxMixin):
    def __init__(
        self,
        caps: SandboxCapabilities | None = None,
        *,
        exit_code: int = 0,
        run_user: str | None = "cubeplex",
        run_uid: int | None = 1000,
        run_gid: int | None = 1000,
    ) -> None:
        self._caps = caps or SandboxCapabilities(terminal=True, browser=True)
        self._exit_code = exit_code
        self._run_user = run_user
        self._run_uid = run_uid
        self._run_gid = run_gid
        self.commands: list[tuple[str, bool]] = []

    @property
    def id(self) -> str:
        return "sbx_posix"

    @property
    def workdir(self) -> str:
        return "/workspace"

    @property
    def provider_name(self) -> str:
        return "stub-posix"

    @property
    def capabilities(self) -> SandboxCapabilities:
        return self._caps

    async def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
        envs: dict[str, str] | None = None,
        as_root: bool = False,
        on_chunk=None,
    ) -> ExecuteResult:
        del timeout, envs, on_chunk
        self.commands.append((command, as_root))
        return ExecuteResult(output="", exit_code=self._exit_code)

    async def upload(self, files: list[tuple[str, bytes]]) -> None:
        del files

    async def download(self, paths: list[str]) -> list[tuple[str, bytes]]:
        del paths
        return []

    async def close(self) -> None:
        return


def test_sandbox_capabilities_contract_has_exact_nine_fields() -> None:
    names = [f.name for f in fields(SandboxCapabilities)]
    assert names == [
        "background",
        "reconnect",
        "persistent_volume",
        "pause",
        "reset_rootfs",
        "terminal",
        "browser",
        "egress_proxy",
        "network_policy",
    ]
    assert "fork" not in names
    default_caps = SandboxCapabilities()
    assert all(getattr(default_caps, name) is False for name in names)


def test_error_and_file_entry_types() -> None:
    err = SandboxCapabilityUnsupportedError("terminal", "local")
    assert isinstance(err, SandboxError)
    assert err.capability == "terminal"
    assert err.provider == "local"
    assert "local" in str(err)
    assert "terminal" in str(err)

    assert issubclass(SandboxFileTooLargeError, SandboxError)

    now = datetime.now(UTC)
    entry = FileEntry(
        path="/workspace/hello.txt",
        name="hello.txt",
        is_dir=False,
        size=12,
        modified_at=now,
    )
    assert entry.modified_at.tzinfo is UTC


def test_sandbox_legacy_capability_methods_delegate() -> None:
    caps = SandboxCapabilities(background=True, reconnect=False, pause=True)
    sb = _StubBaseSandbox(caps)
    assert sb.supports_background() is True
    assert sb.supports_background_reconnect() is False
    assert sb.supports_pause() is True


@pytest.mark.asyncio
async def test_base_sandbox_browser_and_terminal_reject_by_default() -> None:
    sb = _StubBaseSandbox()
    for coro in (
        sb.start_browser(),
        sb.get_browser_endpoint(),
    ):
        with pytest.raises(SandboxCapabilityUnsupportedError) as exc_info:
            await coro
        assert exc_info.value.capability == "browser"
        assert exc_info.value.provider == "stub-base"

    for coro in (
        sb.start_terminal(),
        sb.get_terminal_endpoint(),
    ):
        with pytest.raises(SandboxCapabilityUnsupportedError) as exc_info:
            await coro
        assert exc_info.value.capability == "terminal"
        assert exc_info.value.provider == "stub-base"


@pytest.mark.asyncio
async def test_base_sync_archive_uploads_and_extracts() -> None:
    sb = _StubBaseSandbox()
    await sb.sync_archive(
        b"tar-bytes",
        dest="/workspace/.skills",
        repush_names=["foo"],
        remove_paths=["old-skill"],
    )
    assert sb.uploaded == [(SKILLS_DELTA_TGZ_PATH, b"tar-bytes")]
    assert len(sb.commands) == 1
    cmd, as_root = sb.commands[0]
    assert as_root is False
    assert SKILLS_DELTA_TGZ_PATH in cmd
    assert "/workspace/.skills" in cmd
    assert "foo" in cmd
    assert "old-skill" in cmd


@pytest.mark.asyncio
async def test_base_sync_archive_remove_only_skips_upload() -> None:
    sb = _StubBaseSandbox()
    await sb.sync_archive(None, dest="/workspace/.skills", remove_paths=["stale"])
    assert sb.uploaded == []
    assert len(sb.commands) == 1
    assert "stale" in sb.commands[0][0]


@pytest.mark.asyncio
async def test_base_sync_archive_noop_runs_nothing() -> None:
    sb = _StubBaseSandbox()
    await sb.sync_archive(None, dest="/workspace/.skills")
    assert sb.uploaded == []
    assert sb.commands == []


@pytest.mark.asyncio
async def test_posix_terminal_and_browser_guard_before_shell_commands() -> None:
    sb = _StubPosixSandbox(SandboxCapabilities(terminal=False, browser=False))

    with pytest.raises(SandboxCapabilityUnsupportedError) as term_exc:
        await sb.start_terminal()
    assert term_exc.value.capability == "terminal"
    assert term_exc.value.provider == "stub-posix"
    assert sb.commands == []

    with pytest.raises(SandboxCapabilityUnsupportedError) as brw_exc:
        await sb.start_browser()
    assert brw_exc.value.capability == "browser"
    assert brw_exc.value.provider == "stub-posix"
    assert sb.commands == []

    with pytest.raises(SandboxCapabilityUnsupportedError):
        await sb.get_browser_endpoint()
    with pytest.raises(SandboxCapabilityUnsupportedError):
        await sb.get_terminal_endpoint()


@pytest.mark.asyncio
async def test_posix_ensure_workspace_owner_creates_and_chowns() -> None:
    sb = _StubPosixSandbox()
    await sb.ensure_workspace_owner()
    assert len(sb.commands) == 1
    cmd, as_root = sb.commands[0]
    assert as_root is True
    assert "mkdir -p /workspace" in cmd
    assert "chown 1000:1000 /workspace" in cmd
    assert "-R" not in cmd

    sb_root = _StubPosixSandbox(run_user=None, run_uid=None, run_gid=None)
    await sb_root.ensure_workspace_owner()
    assert sb_root.commands == []


@pytest.mark.asyncio
async def test_posix_panel_endpoint_embeds_provider_claim() -> None:
    sb = _StubPosixSandbox(SandboxCapabilities(terminal=True, browser=True))
    with (
        patch("cubeplex.sandbox.posix.get_panel_base_url", return_value="https://app.test"),
        patch("cubeplex.sandbox.posix.get_panel_secret", return_value="secret-123"),
    ):
        ep = await sb.get_terminal_endpoint()
    assert ep.url.startswith("https://app.test/sandbox-panel/")
    token = ep.url.removeprefix("https://app.test/sandbox-panel/").rstrip("/")
    claims = verify_panel_token(token, secret="secret-123")
    assert claims.sandbox_id == "sbx_posix"
    assert claims.port == Sandbox.TERMINAL_PORT
    assert claims.provider == "stub-posix"
