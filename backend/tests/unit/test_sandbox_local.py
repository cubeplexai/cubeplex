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
