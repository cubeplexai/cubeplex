"""SkillsMiddleware — cubepi port of SkillsMiddleware (M3.c.2).

Watches for ``load_skill`` tool invocations and injects the loaded skill
content into subsequent system prompts so the model "remembers" the skill
for the rest of the conversation.

State lives in ``ctx.context.extra`` (written by ``after_tool_call``)
and is read back via ``extra_ref`` in ``transform_system_prompt``:

    extra["loaded_skills"] → dict[str, str]  — skill_name → content

The ``extra_ref: Callable[[], dict]`` constructor argument follows the
same pattern as ``CompactionMiddleware`` (M3.b.2): the agent factory
passes a closure over ``agent._extra`` so the same dict object is both
written by ``after_tool_call`` and read back by ``transform_system_prompt``
on every subsequent model call, and persisted by ``save_extra`` at
``agent_end``.

Rendering:
    Sorted by skill name for deterministic output (cache discipline).
    Format::

        [Loaded skills]

        ## Skill: <name>

        <content>

        ## Skill: <name>

        <content>
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from cubepi.agent.types import AfterToolCallContext, AfterToolCallResult, AgentContext
from cubepi.middleware.base import Middleware

from cubebox.tools.builtin.load_skill import LoadSkillOutput

_LOADED_SKILLS_KEY = "loaded_skills"
_SKILLS_SECTION_HEADER = "[Loaded skills]"


class SkillsMiddleware(Middleware):
    """cubepi port of SkillsMiddleware (M3.c.2).

    Hooks:
    - ``after_tool_call``: watches for ``load_skill`` invocations; on
      success, stores the skill content in ``extra["loaded_skills"]``.
    - ``transform_system_prompt``: reads ``extra["loaded_skills"]`` and
      appends each skill's content as a system prompt section.

    Constructor args:
        extra_ref:
            Callable returning the live ``extra`` dict associated with the
            current agent run.  The agent factory passes a closure over
            ``agent._extra`` so writes from ``after_tool_call`` are visible
            to ``transform_system_prompt`` on the next model call.
    """

    def __init__(self, *, extra_ref: Callable[[], dict[str, Any]]) -> None:
        self._extra_ref = extra_ref

    async def after_tool_call(
        self,
        ctx: AfterToolCallContext,
        *,
        signal: asyncio.Event | None = None,
    ) -> AfterToolCallResult | None:
        """Watch for load_skill invocations; store content in extra on success.

        If the tool call is not ``load_skill``, or if it errored, or if the
        skill was not loaded (``loaded=False`` in the output JSON), this hook
        is a no-op and returns ``None`` (does not modify the tool result).

        Args:
            ctx: After-tool-call hook context carrying ``tool_call``,
                 ``result``, ``is_error``, and ``args``.
            signal: Cancellation signal (unused).

        Returns:
            ``None`` — this hook only updates ``extra``; it never replaces
            the tool result.
        """
        del signal  # not used

        if ctx.tool_call.name != "load_skill":
            return None

        if ctx.is_error or not ctx.result.content:
            return None

        # load_skill returns a single TextContent whose text is the
        # JSON-serialised LoadSkillOutput.
        raw_text: str = ""
        for block in ctx.result.content:
            if hasattr(block, "text"):
                raw_text = block.text
                break

        if not raw_text:
            return None

        try:
            output = LoadSkillOutput.model_validate_json(raw_text)
        except (json.JSONDecodeError, ValueError):
            # Malformed tool result — skip silently.
            return None

        if not output.loaded or not output.content:
            return None

        # Write into the live extra dict so transform_system_prompt can read it.
        extra = self._extra_ref()
        loaded: dict[str, str] = extra.setdefault(_LOADED_SKILLS_KEY, {})
        loaded[output.skill_name] = output.content

        return None

    async def transform_system_prompt(
        self,
        system_prompt: str,
        *,
        ctx: AgentContext,
        signal: asyncio.Event | None = None,
    ) -> str:
        """Append loaded-skills section to the system prompt.

        Reads ``extra["loaded_skills"]`` and appends each skill's content as
        a named section.  Skills are sorted by name so the output is
        deterministic — identical inputs always produce identical output,
        preserving prompt-cache stability.

        When no skills have been loaded the system prompt is returned
        unchanged.

        Args:
            system_prompt: Current accumulated system prompt string.
            signal: Cancellation signal (unused).

        Returns:
            System prompt with the ``[Loaded skills]`` section appended, or
            the original string when ``loaded_skills`` is empty / absent.
        """
        del ctx, signal  # not used

        extra = self._extra_ref()
        loaded: dict[str, str] = extra.get(_LOADED_SKILLS_KEY, {})
        if not loaded:
            return system_prompt

        sections = [f"## Skill: {name}\n\n{content}" for name, content in sorted(loaded.items())]
        return system_prompt + f"\n\n{_SKILLS_SECTION_HEADER}\n\n" + "\n\n".join(sections)
