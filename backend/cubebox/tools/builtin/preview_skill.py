"""preview_skill tool — fetch SKILL.md content for any candidate (installed or not).

Used by the agent to read a skill before recommending installation. Mirrors
the logic in GET /ws/{ws}/skills/discover/preview without requiring an HTTP
round-trip.
"""

from __future__ import annotations

import json

import httpx
from cubepi.agent.types import AgentTool, AgentToolResult
from cubepi.providers.base import TextContent
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.skills.frontmatter import extract_env_vars, parse_skill_md
from cubebox.skills.service import SkillCatalogService
from cubebox.skills.sources.base import CandidateIdError, decode_candidate_id
from cubebox.skills.sources.registry import SkillsAdapterManager


class PreviewSkillInput(BaseModel):
    candidate_id: str = Field(
        description=(
            "The candidate_id from a find_skills result. "
            "Returns the SKILL.md content so you can describe the skill before suggesting installation."
        )
    )


def _env_vars(content: str) -> list[str]:
    try:
        fm = parse_skill_md(content)
        return extract_env_vars(fm.raw_metadata)
    except Exception:  # noqa: BLE001
        return []


def create_preview_skill_tool(
    *,
    session: AsyncSession,
    registry: SkillsAdapterManager,
    catalog: SkillCatalogService,
    org_id: str,
) -> AgentTool[PreviewSkillInput]:
    async def _execute(
        tool_call_id: str,
        args: PreviewSkillInput,
        *,
        signal: object = None,
        on_update: object = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update

        try:
            kind, source_id, source_ref = decode_candidate_id(args.candidate_id)
        except CandidateIdError:
            return AgentToolResult(content=[TextContent(text="BAD_CANDIDATE_ID")], is_error=True)

        if kind == "local":
            from cubebox.repositories.skill import (
                OrgPreinstalledTombstoneRepository,
                SkillRepository,
                SkillVersionRepository,
            )

            skill = await SkillRepository(session).get(source_ref)
            if skill is None or not (
                skill.source == "preinstalled" or skill.owner_org_id == org_id
            ):
                return AgentToolResult(content=[TextContent(text="SKILL_NOT_FOUND")], is_error=True)
            if skill.source == "preinstalled":
                tombstone = await OrgPreinstalledTombstoneRepository(session).get(org_id, skill.id)
                if tombstone is not None:
                    return AgentToolResult(
                        content=[TextContent(text="SKILL_NOT_FOUND")], is_error=True
                    )
            sv = await SkillVersionRepository(session).find(skill.id, skill.current_version)
            if sv is None:
                return AgentToolResult(
                    content=[TextContent(text="SKILL_VERSION_NOT_FOUND")], is_error=True
                )
            content = await catalog.fetch_skill_md(sv.id)
            payload = {
                "candidate_id": args.candidate_id,
                "name": skill.name,
                "content": content,
                "env_vars": _env_vars(content),
            }
            return AgentToolResult(content=[TextContent(text=json.dumps(payload))])

        # Remote path
        adapter = registry.adapter_by_id(source_id)
        if adapter is None:
            return AgentToolResult(content=[TextContent(text="SOURCE_NOT_FOUND")], is_error=True)
        try:
            files = await adapter.fetch(source_ref)
        except (httpx.HTTPError, ValueError) as exc:
            return AgentToolResult(
                content=[TextContent(text=f"REMOTE_FETCH_FAILED: {exc}")], is_error=True
            )
        if "SKILL.md" not in files:
            return AgentToolResult(content=[TextContent(text="SKILL_MD_MISSING")], is_error=True)
        try:
            content = files["SKILL.md"].decode("utf-8")
        except UnicodeDecodeError as exc:
            return AgentToolResult(
                content=[TextContent(text=f"INVALID_UTF8: {exc}")], is_error=True
            )
        slug = source_ref.rsplit("/", 1)[-1]
        try:
            fm = parse_skill_md(content)
            display_name = fm.name or slug
        except Exception:  # noqa: BLE001
            display_name = slug
        payload = {
            "candidate_id": args.candidate_id,
            "name": display_name,
            "content": content,
            "env_vars": _env_vars(content),
        }
        return AgentToolResult(content=[TextContent(text=json.dumps(payload))])

    return AgentTool(
        name="preview_skill",
        description=(
            "Fetch the full SKILL.md of any skill candidate — installed or not. "
            "Use this after find_skills to read what a skill does before recommending installation. "
            "Pass the candidate_id from the find_skills result."
        ),
        parameters=PreviewSkillInput,
        execute=_execute,
    )
