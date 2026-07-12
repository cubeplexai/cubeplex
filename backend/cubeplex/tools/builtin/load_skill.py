"""load_skill tool ported to cubepi.AgentTool (M2.3, skeleton).

M2: tool returns skill content as the tool result (JSON matching LoadSkillOutput).
M3 SkillsMiddleware will:
  - Watch for load_skill tool_result messages
  - Append the loaded skill content to subsequent system prompts via
    transform_system_prompt hook
  - Optionally cache loaded skills in ctx.extra["loaded_skills"]

For M2, the cubepi runtime path can invoke load_skill and get the
content back, but won't yet inject it into future system prompts.
That's M3's coordination.
"""

from __future__ import annotations

from cubepi.agent.types import AgentTool, AgentToolResult
from cubepi.providers.base import TextContent
from pydantic import BaseModel, Field

from cubeplex.skills.sandbox_paths import sandbox_skill_dir
from cubeplex.skills.service import SkillCatalogService


class LoadSkillInput(BaseModel):
    skill_name: str = Field(
        description=(
            "Name of the skill to load. Use the canonical name from your"
            " 'Available skills' list (e.g. 'deep-research' or 'acme:my-skill')."
        )
    )


class LoadSkillOutput(BaseModel):
    skill_name: str
    content: str
    version: str
    loaded: bool
    # Absolute directory the skill's sibling files (scripts/, templates/,
    # references/) are mounted at in the sandbox. Use this path verbatim — do
    # not construct it from the skill name yourself. Empty when not loaded.
    path: str = ""
    error: str | None = None

    def __str__(self) -> str:
        return self.model_dump_json()


def create_load_skill_tool(
    *,
    catalog: SkillCatalogService,
    workspace_id: str,
    org_id: str,
) -> AgentTool[LoadSkillInput]:
    """Build the cubepi load_skill tool.

    Mirrors cubeplex.tools.builtin.load_skill.create_load_skill_tool — same tool
    name, same schema, same business logic.  Only the wrapper shape changes:
    execute accepts (tool_call_id, args, *, signal, on_update).
    """

    async def _execute(
        tool_call_id: str,
        args: LoadSkillInput,
        *,
        signal: object = None,
        on_update: object = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update

        resolved = await catalog.find_enabled_by_name(
            workspace_id, org_id=org_id, name=args.skill_name
        )
        if resolved is None:
            text = LoadSkillOutput(
                skill_name=args.skill_name,
                content="",
                version="",
                loaded=False,
                error=f"Skill '{args.skill_name}' is not enabled in this workspace",
            ).model_dump_json()
            return AgentToolResult(content=[TextContent(text=text)], is_error=True)

        try:
            content = await catalog.fetch_skill_md(resolved.skill_version_id)
        except Exception as e:  # noqa: BLE001
            text = LoadSkillOutput(
                skill_name=args.skill_name,
                content="",
                version=resolved.version,
                loaded=False,
                error=f"Failed to fetch skill content: {e}",
            ).model_dump_json()
            return AgentToolResult(content=[TextContent(text=text)], is_error=True)

        text = LoadSkillOutput(
            skill_name=args.skill_name,
            content=content,
            version=resolved.version,
            loaded=True,
            path=sandbox_skill_dir(resolved.name, resolved.version),
            error=None,
        ).model_dump_json()
        return AgentToolResult(content=[TextContent(text=text)])

    return AgentTool(
        name="load_skill",
        description=(
            "Read a skill's instructions. Returns the SKILL.md content, its "
            "version, and `path` — the exact sandbox directory holding the "
            "skill's sibling files (scripts/, templates/, references/). Use that "
            "`path` verbatim to reference those files; do not build the path from "
            "the skill name. Skills are listed in your system prompt; pass the "
            "exact name."
        ),
        parameters=LoadSkillInput,
        execute=_execute,
    )
