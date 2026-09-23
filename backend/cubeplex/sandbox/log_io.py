"""Safe, separately acknowledged writes for managed command output."""

from __future__ import annotations

import posixpath
import shlex
from dataclasses import dataclass
from uuid import uuid4

from loguru import logger

from cubeplex.sandbox.base import Sandbox

_LOG_DIRECTORY = ".cubeplex"
_APPEND_SCRIPT = """
import os
import sys

parent, name, chunk = sys.argv[1:]
no_follow = getattr(os, "O_NOFOLLOW", 0)
directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | no_follow)
try:
    source = os.open(chunk, os.O_RDONLY | no_follow)
    try:
        target = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND | no_follow,
            0o600,
            dir_fd=directory,
        )
        try:
            while data := os.read(source, 1024 * 1024):
                view = memoryview(data)
                while view:
                    view = view[os.write(target, view):]
            os.fsync(target)
        finally:
            os.close(target)
    finally:
        os.close(source)
finally:
    os.close(directory)
""".strip()


@dataclass(frozen=True)
class AppendOutputResult:
    data_written: bool
    cleanup_done: bool


def _validated_target(sandbox: Sandbox, path: str) -> tuple[str, str]:
    workdir = posixpath.normpath(sandbox.workdir)
    target = posixpath.normpath(path)
    parent, name = posixpath.split(target)
    if (
        parent != posixpath.join(workdir, _LOG_DIRECTORY)
        or not name.endswith(".log")
        or name in ("", ".", "..")
    ):
        raise ValueError("output path is not an internal command log")
    return parent, name


async def _cleanup_chunk(sandbox: Sandbox, chunk_path: str) -> bool:
    try:
        result = await sandbox.execute(
            f"rm -f -- {shlex.quote(chunk_path)}",
            timeout=30,
        )
    except Exception:
        logger.exception("failed to clean command log chunk {}", chunk_path)
        return False
    if result.exit_code not in (0, None):
        logger.warning(
            "command log chunk cleanup exited {} for {}",
            result.exit_code,
            chunk_path,
        )
        return False
    return True


async def append_output(sandbox: Sandbox, path: str, data: str | bytes) -> AppendOutputResult:
    """Append output without acknowledging provider data before the write succeeds."""

    parent, name = _validated_target(sandbox, path)
    encoded = data.encode() if isinstance(data, str) else data
    prepare = (
        f"umask 077; mkdir -p -- {shlex.quote(parent)} && "
        f"test -d {shlex.quote(parent)} && test ! -L {shlex.quote(parent)}"
    )
    try:
        prepared = await sandbox.execute(prepare, timeout=30)
    except Exception:
        logger.exception("failed to prepare command log directory {}", parent)
        return AppendOutputResult(data_written=False, cleanup_done=True)
    if prepared.exit_code not in (0, None):
        logger.warning("unsafe command log directory rejected: {}", parent)
        return AppendOutputResult(data_written=False, cleanup_done=True)

    chunk_path = f"/tmp/.cubeplex-log-{uuid4().hex}"
    try:
        await sandbox.upload([(chunk_path, encoded)])
        command = " ".join(
            (
                "python3 -c",
                shlex.quote(_APPEND_SCRIPT),
                shlex.quote(parent),
                shlex.quote(name),
                shlex.quote(chunk_path),
            )
        )
        written = await sandbox.execute(command, timeout=30)
        data_written = written.exit_code in (0, None)
        if not data_written:
            logger.warning("command log append exited {} for {}", written.exit_code, path)
    except Exception:
        logger.exception("failed to append command output to {}", path)
        data_written = False

    cleanup_done = await _cleanup_chunk(sandbox, chunk_path)
    return AppendOutputResult(data_written=data_written, cleanup_done=cleanup_done)
