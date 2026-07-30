"""Unit tests: the env file terminal shells source.

ttyd freezes its environ at start, so start_terminal writes the current env to a
file that the image's /etc/bash.bashrc sources. That file is shell code, so the
tests here are about it staying *only* data: values must survive verbatim and
names that aren't POSIX names must never reach it.
"""

from __future__ import annotations

import base64
import shlex
import subprocess
from unittest.mock import AsyncMock, MagicMock

import pytest
from opensandbox.models.execd import RunCommandOpts

from cubeplex.sandbox.opensandbox import OpenSandbox


def _make_backend() -> tuple[OpenSandbox, list[str]]:
    """Return an OpenSandbox whose fake execd records the commands it is given."""
    raw = MagicMock()
    commands: list[str] = []

    async def fake_run(
        command: str, *, opts: RunCommandOpts | None = None, **_: object
    ) -> MagicMock:
        commands.append(command)
        result = MagicMock()
        result.logs.stdout = []
        result.logs.stderr = []
        result.id = None
        return result

    raw.commands.run = fake_run
    raw.files.write_file = AsyncMock()
    raw.id = "sbx-test"

    backend = OpenSandbox(sandbox=raw, workdir="/workspace")
    return backend, commands


def _written_script(command: str) -> str:
    """Recover the file contents from the base64 the write command carries."""
    tokens = shlex.split(command)
    return base64.b64decode(tokens[tokens.index("%s") + 1]).decode()


def _eval(script: str, var: str) -> str:
    """Source the generated script in a real bash and read one variable back."""
    out = subprocess.run(
        ["bash", "-c", f'{script}\nprintf %s "${var}"'],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout


@pytest.mark.asyncio
async def test_hostile_value_is_data_not_code() -> None:
    """A value that looks like shell must come back byte-for-byte, unexecuted."""
    backend, commands = _make_backend()
    hostile = "'; touch /tmp/cubeplex-pwned; echo '"
    backend.set_run_env({"TOKEN": hostile})

    await backend._write_terminal_env()

    script = _written_script(commands[0])
    assert _eval(script, "TOKEN") == hostile


@pytest.mark.asyncio
async def test_value_with_newline_and_quotes_survives() -> None:
    backend, commands = _make_backend()
    awkward = "line1\nline2 'quoted' \"double\" $HOME `id`"
    backend.set_run_env({"BLOB": awkward})

    await backend._write_terminal_env()

    assert _eval(_written_script(commands[0]), "BLOB") == awkward


@pytest.mark.asyncio
async def test_name_that_is_not_a_posix_name_is_dropped() -> None:
    """The service rejects these on write; this is the independent second gate."""
    backend, commands = _make_backend()
    backend.set_run_env({"GOOD": "1", "BAD='; id; X='": "2", "1BAD": "3", "B AD": "4"})

    await backend._write_terminal_env()

    script = _written_script(commands[0])
    assert script == "export GOOD=1\n"


@pytest.mark.asyncio
async def test_env_file_is_written_before_ttyd_starts() -> None:
    """A fresh sandbox's very first terminal must already see the env."""
    backend, commands = _make_backend()
    backend.set_run_env({"TOKEN": "cbxref_AAAA"})

    await backend.start_terminal()

    assert "/run/cubeplex/sandbox-env.sh" in commands[0]
    assert "ttyd" in commands[1]


@pytest.mark.asyncio
async def test_file_is_published_by_rename_never_truncated_in_place() -> None:
    """`> target` empties the file before base64 writes a byte. A shell starting in
    that window sources a partial file — the silent half-configured terminal this
    whole change exists to prevent. Publish via rename so a reader sees one or the
    other, never a torn file."""
    backend, commands = _make_backend()
    backend.set_run_env({"TOKEN": "cbxref_AAAA"})

    await backend._write_terminal_env()

    command = commands[0]
    target = "/run/cubeplex/sandbox-env.sh"
    assert f"> {target}" not in command, "must not redirect onto the live file"
    assert f'mv -f "$tmp" {target}' in command
    assert "mktemp" in command, "concurrent writers need separate scratch files"


@pytest.mark.asyncio
async def test_empty_env_still_rewrites_the_file() -> None:
    """Rewritten in full every time, so a deleted entry stops being exported."""
    backend, commands = _make_backend()
    backend.set_run_env({})

    await backend._write_terminal_env()

    assert _written_script(commands[0]) == "\n"
