"""SkillsMiddleware — injects available skills into system prompt."""

import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool
from loguru import logger

from cubebox.middleware._utils import append_to_system_message
from cubebox.prompts.skills import SKILLS_PROMPT_TEMPLATE
from cubebox.sandbox.skills import CONTAINER_SKILLS_ROOT


@dataclass
class SkillSpec:
    """A skill available to the agent."""

    name: str
    description: str
    path: str | None = None  # path to SKILL.md, if file-backed


def load_builtin_skills(builtin_dir: Path) -> list["SkillSpec"]:
    """Load SkillSpec objects from SKILL.md files in builtin_dir.

    Each subdirectory of builtin_dir that contains a SKILL.md is treated as a skill.
    The name and description are extracted from the YAML frontmatter.

    Args:
        builtin_dir: Path to the directory containing skill subdirectories.

    Returns:
        List of SkillSpec instances for all valid skills found.
    """
    skills: list[SkillSpec] = []
    if not builtin_dir.exists():
        return skills

    for skill_dir in sorted(builtin_dir.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        try:
            text = skill_md.read_text(encoding="utf-8")
            match = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
            if not match:
                continue
            frontmatter = match.group(1)
            name_match = re.search(r"^name:\s*(.+)$", frontmatter, re.MULTILINE)
            desc_match = re.search(r"^description:\s*(.+)$", frontmatter, re.MULTILINE)
            if name_match and desc_match:
                skills.append(
                    SkillSpec(
                        name=name_match.group(1).strip(),
                        description=desc_match.group(1).strip(),
                        path=f"{CONTAINER_SKILLS_ROOT}/{skill_dir.name}/SKILL.md",
                    )
                )
        except Exception as exc:
            logger.warning("Failed to load skill from {}: {}", skill_dir.name, exc)

    return skills


class SkillsMiddleware(AgentMiddleware[Any, Any, Any]):
    """Injects available skills into the system prompt each model call."""

    tools: Sequence[BaseTool] = []

    def __init__(self, *, skills: list[SkillSpec]) -> None:
        self._skills = skills

    def _build_prompt(self) -> str:
        if not self._skills:
            return ""
        skills_list = "\n".join(
            f"- **{s.name}** (`{s.path}`): {s.description}"
            if s.path
            else f"- **{s.name}**: {s.description}"
            for s in self._skills
        )
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
