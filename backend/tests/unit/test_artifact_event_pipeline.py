"""Pipeline test for the live artifact SSE event.

Regression gate: save_artifact embedded the artifact only in its tool_result
content and no standalone ``artifact`` event was ever produced, so the frontend
artifact store stayed empty until a page reload (clicking the card opened an
empty preview panel). This walks the full deterministic chain — produce →
translate → persist → replay — without a live LLM, asserting the artifact event
survives every hop so both the live store and reconnect/replay are consistent.
"""

from __future__ import annotations

import json

from cubepi import AgentToolResult
from cubepi.agent.types import ToolExecutionEndEvent
from cubepi.providers.base import TextContent

from cubeplex.agents.schemas import ArtifactEvent
from cubeplex.agents.stream import convert_agent_event_to_sse
from cubeplex.streams.run_manager import _dicts_to_sse_events, cubepi_dict_to_agent_event

TS = "2026-05-27T00:00:00+00:00"


def test_save_artifact_event_survives_produce_translate_persist_replay() -> None:
    artifact = {
        "id": "art_1",
        "conversation_id": "conv_1",
        "name": "report.html",
        "artifact_type": "website",
        "path": "/work/report",
        "entry_file": "index.html",
        "version": 1,
    }
    tool_evt = ToolExecutionEndEvent(
        tool_call_id="tc-1",
        tool_name="save_artifact",
        result=AgentToolResult(
            content=[TextContent(text=json.dumps({"action": "created", "artifact": artifact}))]
        ),
    )

    # 1. Produce: agent event → SSE dicts (tool_result + artifact).
    sse_dicts = convert_agent_event_to_sse(tool_evt)
    artifact_dicts = [d for d in sse_dicts if d["type"] == "artifact"]
    assert len(artifact_dicts) == 1

    # 2. Translate: live dict → typed event published on the stream.
    live_event = cubepi_dict_to_agent_event(artifact_dicts[0], TS)
    assert isinstance(live_event, ArtifactEvent)
    assert live_event.data == {"action": "created", "artifact": artifact}

    # 3. Persist: what append_run_event writes to the Redis stream.
    persisted = live_event.model_dump()
    assert persisted["type"] == "artifact"

    # 4. Replay: reconnect / history rebuild reconstructs the same event.
    replayed = _dicts_to_sse_events([persisted])
    assert len(replayed) == 1
    assert isinstance(replayed[0], ArtifactEvent)
    assert replayed[0].data == {"action": "created", "artifact": artifact}
