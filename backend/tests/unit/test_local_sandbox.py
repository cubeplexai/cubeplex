import stat
from pathlib import Path

import pytest

from cubeplex.sandbox.local import LocalSandbox


@pytest.mark.asyncio
async def test_local_sandbox_upload_sets_regular_file_mode(tmp_path: Path) -> None:
    sandbox = LocalSandbox(workdir=str(tmp_path))
    file_path = str(tmp_path / "test.txt")
    await sandbox.upload([(file_path, b"hello world")])

    mode = stat.S_IMODE(Path(file_path).stat().st_mode)
    assert mode == 0o644


@pytest.mark.asyncio
async def test_local_sandbox_upload_sets_script_file_mode(tmp_path: Path) -> None:
    sandbox = LocalSandbox(workdir=str(tmp_path))
    file_path = str(tmp_path / "run.sh")
    await sandbox.upload([(file_path, b"#!/bin/bash\necho hi")])

    mode = stat.S_IMODE(Path(file_path).stat().st_mode)
    assert mode == 0o755
