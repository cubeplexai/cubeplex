"""Unit tests: OpenSandbox.execute passes env into commands.run via RunCommandOpts.

Tests:
- set_run_env stores the env; execute forwards it via opts.envs.
- Per-call envs (execute(..., envs=...)) merge on top of run-level env (per-call wins).
- Empty run env + no per-call envs → opts.envs is None (not an empty dict).
- Default agent commands run as cubeplex uid/gid 1000; as_root clears them.
- upload stamps owner/group to the sandbox user.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from opensandbox.models.execd import RunCommandOpts

from cubeplex.sandbox.opensandbox import OpenSandbox


def _make_backend(
    *,
    run_uid: int | None = 1000,
    run_gid: int | None = 1000,
    run_user: str | None = "cubeplex",
) -> tuple[OpenSandbox, MagicMock]:
    """Return an OpenSandbox with a fake _sandbox that records commands.run calls."""
    raw = MagicMock()
    # commands.run is async; capture the opts it receives
    run_calls: list[tuple[str, RunCommandOpts | None]] = []

    async def fake_run(
        command: str, *, opts: RunCommandOpts | None = None, **_: object
    ) -> MagicMock:
        run_calls.append((command, opts))
        result = MagicMock()
        result.logs.stdout = []
        result.logs.stderr = []
        result.id = None
        return result

    raw.commands.run = fake_run
    raw.files.write_file = AsyncMock()
    raw.id = "sbx-test"

    backend = OpenSandbox(
        sandbox=raw,
        workdir="/workspace",
        run_uid=run_uid,
        run_gid=run_gid,
        run_user=run_user,
    )
    # attach run_calls so the test can inspect them
    backend._test_run_calls = run_calls  # type: ignore[attr-defined]
    return backend, raw


@pytest.mark.asyncio
async def test_set_run_env_forwarded_to_commands_run() -> None:
    """set_run_env → execute passes env in opts.envs."""
    backend, _ = _make_backend()

    backend.set_run_env(
        {"GITHUB_TOKEN": "cbxref_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", "LOG_LEVEL": "info"}
    )
    await backend.execute("echo hi")

    calls = backend._test_run_calls  # type: ignore[attr-defined]
    assert len(calls) == 1
    _, opts = calls[0]
    assert opts is not None
    assert isinstance(opts, RunCommandOpts)
    assert opts.envs is not None
    assert opts.envs["GITHUB_TOKEN"] == "cbxref_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    assert opts.envs["LOG_LEVEL"] == "info"


@pytest.mark.asyncio
async def test_execute_forwards_timeout_to_run_opts() -> None:
    backend, _ = _make_backend()

    await backend.execute("sleep 9", timeout=120)

    _, opts = backend._test_run_calls[0]  # type: ignore[attr-defined]
    assert opts is not None
    assert opts.timeout is not None
    assert opts.timeout.total_seconds() == 120


@pytest.mark.asyncio
async def test_execute_timeout_error_returns_marker() -> None:
    backend, raw = _make_backend()

    async def boom(command: str, *, opts: RunCommandOpts | None = None, **_: object) -> MagicMock:
        del command, opts
        from opensandbox.exceptions import SandboxException

        raise SandboxException("command timed out after 120s")

    raw.commands.run = boom
    result = await backend.execute("sleep 999", timeout=120)
    assert result.output == "[timeout]"
    assert result.exit_code == -1


@pytest.mark.asyncio
async def test_per_call_envs_merge_and_win_over_run_env() -> None:
    """Per-call envs override run-level env; both are present in opts.envs."""
    backend, _ = _make_backend()

    backend.set_run_env({"BASE": "base_val", "OVERRIDE": "run_level"})
    await backend.execute("echo hi", envs={"OVERRIDE": "per_call", "EXTRA": "extra_val"})

    calls = backend._test_run_calls  # type: ignore[attr-defined]
    assert len(calls) == 1
    _, opts = calls[0]
    assert opts is not None
    assert opts.envs is not None
    # Per-call wins
    assert opts.envs["OVERRIDE"] == "per_call"
    # Run-level base key still present
    assert opts.envs["BASE"] == "base_val"
    # Per-call extra key present
    assert opts.envs["EXTRA"] == "extra_val"


@pytest.mark.asyncio
async def test_empty_run_env_and_no_per_call_envs_passes_none() -> None:
    """With no run env and no per-call envs, opts.envs is None (not {})."""
    backend, _ = _make_backend()

    # default: _run_env is {}
    await backend.execute("echo hi")

    calls = backend._test_run_calls  # type: ignore[attr-defined]
    assert len(calls) == 1
    _, opts = calls[0]
    assert opts is not None
    assert opts.envs is None, f"Expected opts.envs=None for empty env, got {opts.envs!r}"


@pytest.mark.asyncio
async def test_working_directory_always_set() -> None:
    """opts.working_directory is always set from _workdir."""
    backend, _ = _make_backend()

    await backend.execute("echo hi")

    calls = backend._test_run_calls  # type: ignore[attr-defined]
    _, opts = calls[0]
    assert opts is not None
    assert opts.working_directory == "/workspace"


@pytest.mark.asyncio
async def test_default_execute_runs_as_cubeplex_uid() -> None:
    """Agent commands default to uid/gid 1000 (cubeplex)."""
    backend, _ = _make_backend()

    await backend.execute("whoami")

    calls = backend._test_run_calls  # type: ignore[attr-defined]
    _, opts = calls[0]
    assert opts is not None
    assert opts.uid == 1000
    assert opts.gid == 1000


@pytest.mark.asyncio
async def test_as_root_clears_uid_gid() -> None:
    """as_root=True leaves uid/gid unset so the control plane stays root."""
    backend, _ = _make_backend()

    await backend.execute("whoami", as_root=True)

    calls = backend._test_run_calls  # type: ignore[attr-defined]
    _, opts = calls[0]
    assert opts is not None
    assert opts.uid is None
    assert opts.gid is None


@pytest.mark.asyncio
async def test_start_browser_runs_as_root() -> None:
    """Browser stack needs root for supervisord privilege drop / chown."""
    backend, _ = _make_backend()

    await backend.start_browser()

    calls = backend._test_run_calls  # type: ignore[attr-defined]
    assert len(calls) == 2
    cmd, opts = calls[0]
    assert cmd == "/usr/local/bin/start-browser.sh"
    assert opts is not None
    assert opts.uid is None
    assert opts.gid is None
    install_cmd, install_opts = calls[1]
    assert "cubeplex-focus-browser-tab" in install_cmd
    assert install_opts is not None
    assert install_opts.uid is None
    assert install_opts.gid is None


@pytest.mark.asyncio
async def test_upload_chowns_by_uid() -> None:
    """Upload writes bytes then chowns by numeric uid (files API is root)."""
    backend, raw = _make_backend()

    await backend.upload([("/workspace/hello.txt", b"hi")])

    raw.files.write_file.assert_awaited_once_with("/workspace/hello.txt", b"hi")
    calls = backend._test_run_calls  # type: ignore[attr-defined]
    assert len(calls) == 1
    cmd, opts = calls[0]
    assert "chown 1000:1000" in cmd
    assert "/workspace/hello.txt" in cmd
    assert "-R" not in cmd
    assert opts is not None
    assert opts.uid is None  # as_root


@pytest.mark.asyncio
async def test_ensure_workspace_owner_chowns_mount_point_only() -> None:
    """Create-time fix: chown the workdir, not a recursive tree walk."""
    backend, _ = _make_backend()

    await backend.ensure_workspace_owner()

    calls = backend._test_run_calls  # type: ignore[attr-defined]
    assert len(calls) == 1
    cmd, opts = calls[0]
    assert cmd.startswith("chown 1000:1000 ")
    assert "-R" not in cmd
    assert "/workspace" in cmd
    assert opts is not None
    assert opts.uid is None


@pytest.mark.asyncio
async def test_null_run_uid_skips_privilege_drop() -> None:
    """run_uid=None keeps prior root-by-default behaviour."""
    backend, _ = _make_backend(run_uid=None, run_gid=None, run_user=None)

    await backend.execute("whoami")

    calls = backend._test_run_calls  # type: ignore[attr-defined]
    _, opts = calls[0]
    assert opts is not None
    assert opts.uid is None
    assert opts.gid is None
