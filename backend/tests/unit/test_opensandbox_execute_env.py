"""Unit tests: OpenSandbox.execute passes env into commands.run via RunCommandOpts.

Tests:
- set_run_env stores the env; execute forwards it via opts.envs.
- Per-call envs (execute(..., envs=...)) merge on top of run-level env (per-call wins).
- Empty run env + no per-call envs → opts.envs is None (not an empty dict).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from opensandbox.models.execd import RunCommandOpts

from cubeplex.sandbox.opensandbox import OpenSandbox


def _make_backend() -> tuple[OpenSandbox, MagicMock]:
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
    raw.id = "sbx-test"

    backend = OpenSandbox(sandbox=raw, workdir="/workspace")
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
