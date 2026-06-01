"""find_skills tool — read-only conversational skill discovery (cubepi AgentTool).

Mirrors load_skill.py's wrapper shape. Returns ranked candidates as JSON
(descriptions only, never full SKILL.md). The agent passes a candidate_id
back to the install route (a user-confirmed action), or
load_skill(canonical_name) for already-enabled candidates.
"""

from __future__ import annotations

import json

from cubepi.agent.types import AgentTool, AgentToolResult
from cubepi.providers.base import TextContent
from pydantic import BaseModel, Field

from cubebox.skills.discovery import SkillDiscoveryService


class FindSkillsInput(BaseModel):
    query: str = Field(description="Plain-language description of the capability you need.")
    limit: int = Field(default=5, ge=1, le=20)


def create_find_skills_tool(*, discovery: SkillDiscoveryService) -> AgentTool[FindSkillsInput]:
    async def _execute(
        tool_call_id: str,
        args: FindSkillsInput,
        *,
        signal: object = None,
        on_update: object = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        cands = await discovery.discover(args.query, limit=args.limit)
        payload = {
            "candidates": [
                {
                    "candidate_id": c.candidate_id,
                    "name": c.name,
                    "canonical_name": c.canonical_name,
                    "description": c.description,
                    "source": c.source_kind,
                    "source_name": c.source_name,
                    "repo": c.repo,
                    "trust": c.trust.value,
                    "install_state": c.install_state,
                    "unvetted": c.source_kind == "remote" and c.trust.value != "official",
                }
                for c in cands
            ],
            "hint": (
                "To use an 'enabled' candidate now, call load_skill(canonical_name). "
                "To install an 'in_catalog' or 'available' candidate: present it to the "
                "user with preview_skill(candidate_id) so they can see what it does, then "
                "call install_skill(candidate_id) only when the user explicitly asks to install. "
                "Never install silently."
            ),
        }
        return AgentToolResult(content=[TextContent(text=json.dumps(payload))])

    return AgentTool(
        name="find_skills",
        description=(
            "Search available skills (your org's catalog + registered remote "
            "registries) by a plain-language need. Read-only: returns ranked "
            "candidates with descriptions; it never installs anything."
        ),
        parameters=FindSkillsInput,
        execute=_execute,
    )
