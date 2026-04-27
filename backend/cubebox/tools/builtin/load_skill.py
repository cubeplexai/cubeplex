"""load_skill — read a skill's SKILL.md content via the catalog service.

Backend-only: never touches the sandbox. See spec § 7.2.
"""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from cubebox.skills.service import SkillCatalogService


class LoadSkillInput(BaseModel):
    skill_name: str = Field(
        description="Name of the skill to load. Use the canonical name from your"
        " 'Available skills' list (e.g. 'deep-research' or 'acme:my-skill')."
    )


class LoadSkillOutput(BaseModel):
    skill_name: str
    content: str
    version: str
    loaded: bool
    error: str | None = None

    def __str__(self) -> str:
        return self.model_dump_json()


def create_load_skill_tool(
    *,
    catalog: SkillCatalogService,
    workspace_id: str,
    org_id: str,
) -> StructuredTool:
    async def _load_skill(skill_name: str) -> str:
        resolved = await catalog.find_enabled_by_name(workspace_id, org_id=org_id, name=skill_name)
        if resolved is None:
            return LoadSkillOutput(
                skill_name=skill_name,
                content="",
                version="",
                loaded=False,
                error=f"Skill '{skill_name}' is not enabled in this workspace",
            ).model_dump_json()
        try:
            content = await catalog.fetch_skill_md(resolved.skill_version_id)
        except Exception as e:
            return LoadSkillOutput(
                skill_name=skill_name,
                content="",
                version=resolved.version,
                loaded=False,
                error=f"Failed to fetch skill content: {e}",
            ).model_dump_json()
        return LoadSkillOutput(
            skill_name=skill_name,
            content=content,
            version=resolved.version,
            loaded=True,
            error=None,
        ).model_dump_json()

    return StructuredTool.from_function(
        coroutine=_load_skill,
        name="load_skill",
        description=(
            "Read a skill's instructions. Returns SKILL.md content plus version. "
            "Skills are listed in your system prompt; pass the exact name."
        ),
        args_schema=LoadSkillInput,
    )
