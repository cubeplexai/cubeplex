"""skills capability — find/preview/install operations.

Unlike SCHEDULED_TASKS_CAPABILITY (a module-level constant), the skills
capability is built per-run via build_skills_capability(deps) because its
handlers must close over run-scoped dependencies: a SkillCatalogService,
the catalog AsyncSession, a SkillsAdapterManager (itself built async at
run start), and the org id/slug.

load_skill is intentionally NOT migrated here. It is runtime infrastructure
(SkillsMiddleware intercepts its result to append SKILL.md to the next
system prompt) and stays wired directly in run_manager.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.agents.actions.context import ScopeContext
from cubebox.agents.actions.types import (
    AgentCapability,
    AgentOperation,
)
from cubebox.skills.discovery import SkillDiscoveryService
from cubebox.skills.service import SkillCatalogService
from cubebox.skills.sources.registry import SkillsAdapterManager

# Module-level alias so tests can monkeypatch it.
_SkillDiscoveryService = SkillDiscoveryService


@dataclass(frozen=True)
class SkillDeps:
    """Run-scoped dependencies for the skills capability.

    Constructed once at the start of a cubepi run when the catalog is
    reachable; captured in the handler closures returned by
    build_skills_capability.
    """

    catalog: SkillCatalogService
    catalog_session: AsyncSession
    registry: SkillsAdapterManager
    org_id: str
    org_slug: str
    workspace_id: str | None


# --- Input models per operation ---


class FindInput(BaseModel):
    query: str = Field(
        description="Plain-language description of the capability you need.",
    )
    limit: int = Field(default=5, ge=1, le=20)


class PreviewInput(BaseModel):
    candidate_id: str = Field(
        description=(
            "The candidate_id from a find result. Returns the SKILL.md "
            "content so you can describe the skill before suggesting installation."
        ),
    )


class InstallInput(BaseModel):
    candidate_id: str = Field(
        description=(
            "The candidate_id from a find result. "
            "Only call this after the user has explicitly confirmed they want to install."
        ),
    )


# --- Handler implementation stubs (Task 2/3/4 fill these in) ---


async def _handle_find_impl(
    deps: SkillDeps, ctx: ScopeContext, session: Any, inp: FindInput
) -> Any:
    del ctx, session  # uses deps' run-scoped registry, not the per-call session
    discovery = _SkillDiscoveryService(deps.registry)
    cands = await discovery.discover(inp.query, limit=inp.limit)
    return {
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
                "install_count": c.install_count,
                "unvetted": c.source_kind == "remote" and c.trust.value != "official",
            }
            for c in cands
        ],
        "hint": (
            "To use an 'enabled' candidate now, call load_skill(canonical_name). "
            "To install an 'in_catalog' or 'available' candidate: present it to the "
            "user with skills(operation='preview', candidate_id=...) so they can see "
            "what it does, then call skills(operation='install', candidate_id=...) "
            "only when the user explicitly asks to install. Never install silently."
        ),
    }


async def _handle_preview_impl(
    deps: SkillDeps, ctx: ScopeContext, session: Any, inp: PreviewInput
) -> Any:
    raise NotImplementedError("Task 3 fills this in")


async def _handle_install_impl(
    deps: SkillDeps, ctx: ScopeContext, session: Any, inp: InstallInput
) -> Any:
    raise NotImplementedError("Task 4 fills this in")


def build_skills_capability(deps: SkillDeps) -> AgentCapability:
    """Build the skills capability with run-scoped deps closed over the handlers."""

    async def find_handler(ctx: ScopeContext, session: Any, inp: FindInput) -> Any:
        return await _handle_find_impl(deps, ctx, session, inp)

    async def preview_handler(ctx: ScopeContext, session: Any, inp: PreviewInput) -> Any:
        return await _handle_preview_impl(deps, ctx, session, inp)

    async def install_handler(ctx: ScopeContext, session: Any, inp: InstallInput) -> Any:
        return await _handle_install_impl(deps, ctx, session, inp)

    return AgentCapability(
        name="skills",
        description=(
            "Search, preview, and install skills available to this workspace. "
            "Use find to discover candidates, preview to read SKILL.md before "
            "suggesting installation, and install only when the user has "
            "explicitly confirmed."
        ),
        operations=[
            AgentOperation(
                name="find",
                description=(
                    "Search available skills (your org's catalog + registered "
                    "remote registries) by a plain-language need. Read-only: "
                    "returns ranked candidates with descriptions; never installs."
                ),
                input_model=FindInput,
                handler=find_handler,
                mutates=False,
            ),
            AgentOperation(
                name="preview",
                description=(
                    "Fetch the full SKILL.md of any candidate — installed or not. "
                    "Use after find to read what a skill does before recommending installation. "
                    "Pass the candidate_id from the find result."
                ),
                input_model=PreviewInput,
                handler=preview_handler,
                mutates=False,
            ),
            AgentOperation(
                name="install",
                description=(
                    "Install a skill candidate into the current workspace. "
                    "Only call this when the user has explicitly asked to install. "
                    "Pass the candidate_id from the find result."
                ),
                input_model=InstallInput,
                handler=install_handler,
                mutates=True,
            ),
        ],
    )
