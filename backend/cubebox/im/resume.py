"""Resume-paused-run wrapper shared by SSE and IM resume paths.

For Task 15 this is a stub raising NotImplementedError. Task 17
implements the real cubepi call.
"""

from __future__ import annotations

from typing import Any


async def resume_paused_run(
    *,
    run_id: str,
    input_kind: str,
    choice: str,
    operator_open_id: str,
    question_id: str = "",
    **_: Any,
) -> bool:
    """Forward a human input to the paused run identified by ``run_id``.

    Returns True if the run accepted the input; False if the run is no
    longer awaiting. Raises NotImplementedError until Task 17 lands the
    real cubepi-side implementation.
    """
    raise NotImplementedError("resume_paused_run is implemented in Task 17 (cubepi resume wrapper)")
