"""Regression tests for the Loguru runtime logging setup."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_SEMAPHORE_TRACE_SCRIPT = r"""
import json
import multiprocessing as mp
import sys
from multiprocessing import resource_tracker

mp.set_start_method("spawn", force=True)

registered = []
unregistered = []
original_register = resource_tracker.register
original_unregister = resource_tracker.unregister


def register(name, rtype):
    if rtype == "semaphore":
        registered.append(name)
    return original_register(name, rtype)


def unregister(name, rtype):
    if rtype == "semaphore":
        unregistered.append(name)
    return original_unregister(name, rtype)


resource_tracker.register = register
resource_tracker.unregister = unregister

from cubeplex.utils import log

log.init(log_path=sys.argv[1], debug=False)
log.shutdown()

print(
    json.dumps(
        {
            "registered": len(registered),
            "unregistered": len(unregistered),
            "missing": sorted(set(registered) - set(unregistered)),
        }
    )
)
"""


def test_loguru_shutdown_releases_enqueue_semaphores(tmp_path: Path) -> None:
    """Loguru enqueue=True handlers must be stopped before a reload child exits."""
    env = os.environ.copy()
    env.pop("CUBEPLEX_TRACE_MP_SEMAPHORES", None)

    result = subprocess.run(
        [sys.executable, "-c", _SEMAPHORE_TRACE_SCRIPT, str(tmp_path / "app.log")],
        capture_output=True,
        check=False,
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["registered"] > 0
    assert payload["missing"] == []
