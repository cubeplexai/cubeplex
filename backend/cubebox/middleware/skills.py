"""SkillsMiddleware — injects available skills into system prompt.

After M3, this is catalog-driven (queries the SkillCatalogService). Old
filesystem-based loader (load_builtin_skills + SkillSpec dataclass) is removed.
"""

from collections.abc import Awaitable, Callable, Sequence
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
from cubebox.skills.service import ResolvedSkill, SkillCatalogService


class SkillsMiddleware(AgentMiddleware[Any, Any, Any]):
    """Injects workspace-enabled skills into the system prompt each model call."""

    tools: Sequence[BaseTool] = []

    def __init__(
        self,
        *,
        catalog: SkillCatalogService,
        workspace_id: str,
        org_id: str,
    ) -> None:
        self._catalog = catalog
        self._workspace_id = workspace_id
        self._org_id = org_id
        self._cached: list[ResolvedSkill] | None = None

    def _build_prompt(self, skills: list[ResolvedSkill]) -> str:
        if not skills:
            return ""
        skills_list = "\n".join(f"- **{s.name}** v{s.version}: {s.description}" for s in skills)
        return SKILLS_PROMPT_TEMPLATE.format(skills_list=skills_list)

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any] | AIMessage]],
    ) -> ModelResponse[Any] | AIMessage:
        if self._cached is None:
            self._cached = await self._catalog.list_enabled_for_workspace(
                self._workspace_id, org_id=self._org_id
            )
        prompt = self._build_prompt(self._cached)
        if not prompt:
            return await handler(request)
        new_system = append_to_system_message(request.system_message, prompt)
        return await handler(request.override(system_message=new_system))
