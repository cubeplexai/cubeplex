"""cubepi-side tool registry (M2).

Exposes builtin tools as cubepi.AgentTool. M2.2/M2.3 add memory + load_skill;
M2.4 adds MCP loading; M3 wires per-conversation dynamic tool sets.

Note: view_images requires per-request DI (org_id, workspace_id, objectstore,
capabilities) and is therefore NOT included in the no-DI list. Callers that
need view_images should call make_view_images_tool(...) from
cubeplex.tools.builtin.view_images and compose it into the tool list at
agent-construction time.
"""

from __future__ import annotations

from cubepi.agent.types import AgentTool

from cubeplex.tools.builtin.calculator import calculator_tool
from cubeplex.tools.builtin.datetime_tool import datetime_tool


def list_builtin_tools() -> list[AgentTool]:  # type: ignore[type-arg]
    """Return cubepi-shaped builtin tools that require no DI.

    M2.2/M2.3 will extend with memory + load_skill (factory functions
    requiring per-request DI).

    view_images is intentionally excluded; it needs org_id, workspace_id,
    objectstore, and LLMCapabilities. Use make_view_images_tool(...) from
    cubeplex.tools.builtin.view_images when constructing an agent that
    handles image attachments.
    """
    return [calculator_tool, datetime_tool]
