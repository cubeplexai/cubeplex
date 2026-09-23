"""Command output is acknowledged only after a safe append."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from cubeplex.sandbox.base import ExecuteResult
from cubeplex.sandbox.local import LocalSandbox
from cubeplex.sandbox.log_io import append_output


async def test_append_output_writes_only_inside_the_internal_log_directory(
    tmp_path: Path,
) -> None:
    sandbox = LocalSandbox(workdir=str(tmp_path))
    log_path = tmp_path / ".cubeplex" / "execute-scmd_1.log"

    first = await append_output(sandbox, str(log_path), "first\n")
    second = await append_output(sandbox, str(log_path), "second\n")

    assert first.data_written and first.cleanup_done
    assert second.data_written and second.cleanup_done
    assert log_path.read_text() == "first\nsecond\n"


async def test_append_output_rejects_paths_outside_the_internal_directory(
    tmp_path: Path,
) -> None:
    sandbox = LocalSandbox(workdir=str(tmp_path))

    with pytest.raises(ValueError, match="internal command log"):
        await append_output(sandbox, str(tmp_path / "result.log"), "escaped")


async def test_append_output_rejects_a_symlinked_internal_directory(tmp_path: Path) -> None:
    sandbox = LocalSandbox(workdir=str(tmp_path))
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".cubeplex").symlink_to(outside, target_is_directory=True)

    result = await append_output(
        sandbox,
        str(tmp_path / ".cubeplex" / "execute-scmd_1.log"),
        "must not follow\n",
    )

    assert not result.data_written
    assert result.cleanup_done
    assert list(outside.iterdir()) == []


async def test_cleanup_failure_does_not_make_confirmed_output_unreadable() -> None:
    sandbox = MagicMock()
    sandbox.workdir = "/workspace"
    sandbox.upload = AsyncMock()
    sandbox.execute = AsyncMock(
        side_effect=(
            ExecuteResult(output="", exit_code=0),
            ExecuteResult(output="", exit_code=0),
            ExecuteResult(output="", exit_code=1),
        )
    )

    result = await append_output(
        sandbox,
        "/workspace/.cubeplex/execute-scmd_1.log",
        "complete\n",
    )

    assert result.data_written is True
    assert result.cleanup_done is False


async def test_write_failure_is_distinct_from_successful_chunk_cleanup() -> None:
    sandbox = MagicMock()
    sandbox.workdir = "/workspace"
    sandbox.upload = AsyncMock()
    sandbox.execute = AsyncMock(
        side_effect=(
            ExecuteResult(output="", exit_code=0),
            ExecuteResult(output="", exit_code=1),
            ExecuteResult(output="", exit_code=0),
        )
    )

    result = await append_output(
        sandbox,
        "/workspace/.cubeplex/execute-scmd_1.log",
        "retry me\n",
    )

    assert result.data_written is False
    assert result.cleanup_done is True


async def test_unknown_append_exit_does_not_acknowledge_output() -> None:
    sandbox = MagicMock()
    sandbox.workdir = "/workspace"
    sandbox.upload = AsyncMock()
    sandbox.execute = AsyncMock(
        side_effect=(
            ExecuteResult(output="", exit_code=0),
            ExecuteResult(output="", exit_code=None),
            ExecuteResult(output="", exit_code=0),
        )
    )

    result = await append_output(
        sandbox,
        "/workspace/.cubeplex/execute-scmd_1.log",
        "unknown write\n",
    )

    assert result.data_written is False
    assert result.cleanup_done is True


async def test_local_output_repeats_until_its_candidate_cursor_is_accepted(
    tmp_path: Path,
) -> None:
    sandbox = LocalSandbox(workdir=str(tmp_path))
    handle = await sandbox.start("printf retryable")
    for _ in range(100):
        status = await sandbox.observe(handle)
        if status.status != "running":
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("local command did not finish")

    first = await sandbox.read_output(handle)
    repeated = await sandbox.read_output(handle)
    assert first.new_output == repeated.new_output == "retryable"
    assert first.log_cursor == repeated.log_cursor

    assert first.log_cursor is not None
    await sandbox.acknowledge_output(handle, first.log_cursor)
    assert sandbox._bg[handle.provider_ref]._buf == b""
    acknowledged = await sandbox.read_output(handle)
    assert acknowledged.new_output == ""
    assert acknowledged.log_cursor == first.log_cursor
