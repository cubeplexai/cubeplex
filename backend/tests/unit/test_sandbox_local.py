import asyncio

import pytest

from cubeplex.sandbox.local import LocalSandbox


@pytest.mark.asyncio
async def test_execute_simple_command():
    sandbox = LocalSandbox()
    result = await sandbox.execute("echo hello")
    assert result.output.strip() == "hello"
    assert result.exit_code == 0


@pytest.mark.asyncio
async def test_execute_exit_code():
    sandbox = LocalSandbox()
    result = await sandbox.execute("exit 1", timeout=5)
    assert result.exit_code == 1


@pytest.mark.asyncio
async def test_execute_timeout_kills_and_returns_marker():
    sandbox = LocalSandbox()
    result = await sandbox.execute("sleep 30", timeout=1)
    assert result.output == "[timeout]"
    assert result.exit_code == -1


@pytest.mark.asyncio
async def test_execute_on_chunk_fires_before_return() -> None:
    sandbox = LocalSandbox()
    chunks: list[str] = []
    result = await sandbox.execute(
        "python3 -c \"import sys,time; print('one', flush=True); "
        "time.sleep(0.15); print('two', flush=True)\"",
        on_chunk=chunks.append,
    )
    assert chunks, "on_chunk must run before execute returns"
    assert "one" in "".join(chunks)
    assert "two" in result.output


@pytest.mark.asyncio
async def test_execute_on_chunk_exception_does_not_fail_command() -> None:
    sandbox = LocalSandbox()

    def _boom(_text: str) -> None:
        raise RuntimeError("chunk listener failed")

    result = await sandbox.execute("echo ok", on_chunk=_boom)
    assert result.exit_code == 0
    assert "ok" in result.output


@pytest.mark.asyncio
async def test_execute_combines_stderr():
    sandbox = LocalSandbox()
    result = await sandbox.execute("echo out && echo err >&2")
    assert "out" in result.output
    assert "err" in result.output


@pytest.mark.asyncio
async def test_upload_and_download(tmp_path):
    sandbox = LocalSandbox(workdir=str(tmp_path))
    content = b"hello world"
    await sandbox.upload([(str(tmp_path / "test.txt"), content)])
    downloaded = await sandbox.download([str(tmp_path / "test.txt")])
    assert downloaded[0][1] == content


@pytest.mark.asyncio
async def test_close_is_noop():
    sandbox = LocalSandbox()
    await sandbox.close()  # should not raise


def test_sandbox_id_is_stable():
    sandbox = LocalSandbox()
    assert sandbox.id == sandbox.id
    assert isinstance(sandbox.id, str)


@pytest.mark.asyncio
async def test_start_returns_before_sleep_exits() -> None:
    sandbox = LocalSandbox()
    handle = await sandbox.start("sleep 2")
    snap = await sandbox.poll(handle)
    assert snap.status == "running"
    await asyncio.sleep(2.2)
    snap = await sandbox.poll(handle)
    assert snap.status == "exited"
    assert snap.exit_code == 0


@pytest.mark.asyncio
async def test_kill_marks_process_killed() -> None:
    sandbox = LocalSandbox()
    handle = await sandbox.start("sleep 30")
    await sandbox.kill(handle)
    snap = await sandbox.poll(handle)
    assert snap.status == "killed"


@pytest.mark.asyncio
async def test_poll_returns_output_from_running_process() -> None:
    sandbox = LocalSandbox()
    handle = await sandbox.start(
        "python3 -c \"import sys,time; print('one', flush=True); "
        "time.sleep(0.2); print('two', flush=True)\""
    )
    seen = ""
    for _ in range(20):
        snap = await sandbox.poll(handle)
        seen += snap.new_output
        if snap.status != "running":
            break
        await asyncio.sleep(0.05)
    assert "one" in seen
    assert "two" in seen
