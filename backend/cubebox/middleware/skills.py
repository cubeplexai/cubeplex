"""SkillsMiddleware — injects available skills into system prompt."""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool

from cubebox.middleware._utils import append_to_system_message
from cubebox.prompts.skills import SKILLS_PROMPT_TEMPLATE


@dataclass
class SkillSpec:
    """A skill available to the agent."""

    name: str
    description: str
    path: str | None = None  # path to SKILL.md, if file-backed


class SkillsMiddleware(AgentMiddleware[Any, Any, Any]):
    """Injects available skills into the system prompt each model call."""

    tools: Sequence[BaseTool] = []

    def __init__(self, *, skills: list[SkillSpec]) -> None:
        self._skills = skills

    def _build_prompt(self) -> str:
        if not self._skills:
            return ""
        skills_list = "\n".join(f"- **{s.name}**: {s.description}" for s in self._skills)
        return SKILLS_PROMPT_TEMPLATE.format(skills_list=skills_list)

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any] | AIMessage]],
    ) -> ModelResponse[Any] | AIMessage:
        prompt = self._build_prompt()
        if not prompt:
            return await handler(request)
        new_system = append_to_system_message(request.system_message, prompt)
        return await handler(request.override(system_message=new_system))
