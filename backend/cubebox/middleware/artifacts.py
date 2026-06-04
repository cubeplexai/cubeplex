"""ArtifactMiddleware.

Implements the cubepi Middleware protocol with two hooks:

- ``tools``: exposes ``save_artifact`` as a ``cubepi.AgentTool`` so the
  graph factory can include it in the tool list passed to the agent.
- ``transform_system_prompt``: queries the artifact registry and appends the
  ARTIFACT_PROMPT + current artifact list to the system prompt. Using the
  system prompt (stable prefix) rather than the per-turn user message is
  critical for prompt-cache correctness.
"""

from __future__ import annotations

import asyncio
import json
import shlex
from collections.abc import Callable
from typing import Any

from cubepi.agent.types import AgentContext, AgentTool, AgentToolResult
from cubepi.middleware.base import Middleware
from cubepi.providers.base import TextContent
from cubepi.types import StructuredValue
from loguru import logger
from pydantic import BaseModel, Field

from cubebox.prompts.artifacts import ARTIFACT_PROMPT
from cubebox.sandbox.base import Sandbox
from cubebox.services.artifact_registration import register_artifact_from_sandbox

# ---------------------------------------------------------------------------
# Input schema for save_artifact
# ---------------------------------------------------------------------------


class _SaveArtifactArgs(BaseModel):
    name: str = Field(description="Human-readable artifact name")
    artifact_type: str = Field(
        description="Type of artifact: file, website, code, document, image, data, or skill"
    )
    path: str = Field(description="Absolute path in sandbox (file or directory)")
    entry_file: str | None = Field(
        default=None,
        description="For directories: the main file to open (e.g. 'index.html')",
    )
    description: str | None = Field(default=None, description="Brief description")
    artifact_id: str | None = Field(
        default=None,
        description="Existing artifact ID to update (omit for new artifact)",
    )


# ---------------------------------------------------------------------------
# Tool factory
# ---------------------------------------------------------------------------


def _make_save_artifact_tool(
    sandbox: Sandbox,
    conversation_id: str,
    org_id: str,
    workspace_id: str,
) -> AgentTool[_SaveArtifactArgs]:
    """Build the save_artifact cubepi.AgentTool backed by sandbox + DB."""

    async def _execute(
        tool_call_id: str,
        args: _SaveArtifactArgs,
        *,
        signal: asyncio.Event | None = None,
        on_update: Callable[[StructuredValue], None] | None = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update

        # Validate path exists in sandbox before delegating to the helper.
        # The helper also checks this, but we catch FileNotFoundError here and
        # return is_error so the tool's external JSON contract is unchanged.
        result = await sandbox.execute(f"test -e {shlex.quote(args.path)}")
        if result.exit_code is not None and result.exit_code != 0:
            return AgentToolResult(
                content=[
                    TextContent(
                        text=json.dumps({"error": f"Path not found in sandbox: {args.path}"})
                    )
                ],
                is_error=True,
            )

        try:
            artifact = await register_artifact_from_sandbox(
                sandbox=sandbox,
                conversation_id=conversation_id,
                org_id=org_id,
                workspace_id=workspace_id,
                name=args.name,
                artifact_type=args.artifact_type,
                path=args.path,
                entry_file=args.entry_file,
                description=args.description,
                artifact_id=args.artifact_id,
            )
        except ValueError as exc:
            return AgentToolResult(
                content=[TextContent(text=json.dumps({"error": str(exc)}))],
                is_error=True,
            )

        # version == 1 → artifact was just created; > 1 → existing was updated.
        action = "created" if artifact.version == 1 else "updated"

        logger.info(
            "Artifact {}: id={}, name={}, type={}, version={}",
            action,
            artifact.id,
            artifact.name,
            artifact.artifact_type,
            artifact.version,
        )

        return AgentToolResult(
            content=[
                TextContent(text=json.dumps({"action": action, "artifact": artifact.to_dict()}))
            ]
        )

    return AgentTool(
        name="save_artifact",
        description=(
            "Register a file or directory created in the sandbox as an artifact "
            "so the user can preview and download it. "
            "First create the files with the execute tool, then call this. "
            "For agent-authored skills, use artifact_type='skill', entry_file='SKILL.md', "
            "and ensure path points to a directory containing SKILL.md at the root."
        ),
        parameters=_SaveArtifactArgs,
        execute=_execute,
    )


# ---------------------------------------------------------------------------
# Middleware class
# ---------------------------------------------------------------------------


class ArtifactMiddleware(Middleware):
    """Registers save_artifact tool and injects artifact prompt into messages.

    Usage::

        mw = ArtifactMiddleware(
            sandbox=sandbox,
            conversation_id=conversation_id,
            org_id=org_id,
            workspace_id=workspace_id,
        )
        # collect mw.tools and pass to Agent(tools=[...])
        # register mw with Agent(middleware=[mw]) for transform_context
    """

    def __init__(
        self,
        *,
        sandbox: Sandbox,
        conversation_id: str,
        org_id: str,
        workspace_id: str,
    ) -> None:
        self.sandbox = sandbox
        self.conversation_id = conversation_id
        self.org_id = org_id
        self.workspace_id = workspace_id

        self._save_artifact_tool: AgentTool[Any] = _make_save_artifact_tool(
            sandbox, conversation_id, org_id, workspace_id
        )

    @property
    def tools(self) -> list[AgentTool[Any]]:
        """Return the cubepi.AgentTool list for this middleware."""
        return [self._save_artifact_tool]

    async def _build_artifact_list(self) -> str:
        """Query DB for existing artifacts and format as a prompt section."""
        from cubebox.db.engine import async_session_maker
        from cubebox.repositories import ArtifactRepository

        async with async_session_maker() as session:
            repo = ArtifactRepository(session, org_id=self.org_id, workspace_id=self.workspace_id)
            artifacts = await repo.list_by_conversation(self.conversation_id)

        if not artifacts:
            return "\n**Existing artifacts:** None yet.\n"

        lines = ["\n**Existing artifacts:**"]
        for a in artifacts:
            lines.append(
                f'- id=`{a.id}` name="{a.name}" type={a.artifact_type} path=`{a.path}` v{a.version}'
            )
        return "\n".join(lines) + "\n"

    async def transform_system_prompt(
        self,
        system_prompt: str,
        *,
        ctx: AgentContext,
        signal: asyncio.Event | None = None,
    ) -> str:
        """Append the artifact prompt + registry state to the system prompt.

        Mirrors ``ArtifactMiddleware.awrap_model_call`` which appends the
        artifact section to the system message. Using the system prompt
        (stable prefix) rather than the per-turn user message is critical
        for prompt-cache correctness: the artifact list changes as artifacts
        accumulate, but it belongs in the stable prefix because the *schema*
        (ARTIFACT_PROMPT) is constant and the *list* is conversation-scoped
        (not turn-scoped). Appending to the user message breaks OpenAI
        auto-cache because the user message content changes every turn.
        """
        del ctx, signal
        artifact_list = await self._build_artifact_list()
        injection = ARTIFACT_PROMPT + artifact_list
        return system_prompt + "\n\n" + injection
