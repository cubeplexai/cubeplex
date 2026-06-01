"""install_skill tool — install a skill candidate for the current workspace.

Only call this when the user has explicitly requested installation in
the current conversation. On success, call load_skill(canonical_name)
immediately to begin using the installed skill.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from cubepi.agent.types import AgentTool, AgentToolResult
from cubepi.providers.base import TextContent
from pydantic import BaseModel, Field

from cubebox.skills.discovery import SkillInstallError, SkillInstallService
from cubebox.skills.sources.base import CandidateIdError, decode_candidate_id


class InstallSkillInput(BaseModel):
    candidate_id: str = Field(
        description=(
            "The candidate_id from a find_skills result. "
            "Only call this after the user has explicitly confirmed they want to install."
        )
    )


def create_install_skill_tool(
    *,
    install_service_factory: Callable[[], SkillInstallService],
) -> AgentTool[InstallSkillInput]:
    async def _execute(
        tool_call_id: str,
        args: InstallSkillInput,
        *,
        signal: object = None,
        on_update: object = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update

        try:
            decode_candidate_id(args.candidate_id)
        except CandidateIdError as exc:
            return AgentToolResult(
                content=[TextContent(text=f"BAD_CANDIDATE_ID: {exc}")], is_error=True
            )

        svc = install_service_factory()
        try:
            result = await svc.install(args.candidate_id)
        except SkillInstallError as exc:
            return AgentToolResult(content=[TextContent(text=str(exc))], is_error=True)

        payload = {
            "installed": True,
            "canonical_name": result.canonical_name,
            "version": result.installed_version,
        }
        return AgentToolResult(content=[TextContent(text=json.dumps(payload))])

    return AgentTool(
        name="install_skill",
        description=(
            "Install a skill candidate into the current workspace. "
            "Only call this when the user has explicitly asked to install. "
            "Pass the candidate_id from a find_skills result. "
            "On success, call load_skill(canonical_name) to use the skill immediately."
        ),
        parameters=InstallSkillInput,
        execute=_execute,
    )
