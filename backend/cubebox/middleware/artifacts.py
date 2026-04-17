"""ArtifactMiddleware — registers the save_artifact tool and injects artifact prompt."""

import json
import mimetypes
import shlex
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool, StructuredTool
from loguru import logger
from pydantic import BaseModel, Field

from cubebox.middleware._utils import append_to_system_message
from cubebox.prompts.artifacts import ARTIFACT_PROMPT
from cubebox.sandbox.base import Sandbox
from cubebox.tools import get_registry


class _SaveArtifactArgs(BaseModel):
    name: str = Field(description="Human-readable artifact name")
    artifact_type: str = Field(
        description="Type of artifact: file, website, code, document, image, or data"
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


def _guess_mime_type(path: str, entry_file: str | None) -> str | None:
    """Guess MIME type from file extension."""
    target = entry_file if entry_file else path
    mime, _ = mimetypes.guess_type(target)
    return mime


def _create_save_artifact_tool(
    sandbox: Sandbox,
    conversation_id: str,
) -> BaseTool:
    """Build the save_artifact tool backed by sandbox + DB."""

    async def _save_artifact(
        name: str,
        artifact_type: str,
        path: str,
        entry_file: str | None = None,
        description: str | None = None,
        artifact_id: str | None = None,
    ) -> str:
        # 1. Validate path exists in sandbox
        result = await sandbox.execute(f"test -e {shlex.quote(path)}")
        if result.exit_code is not None and result.exit_code != 0:
            return json.dumps({"error": f"Path not found in sandbox: {path}"})

        # 2. Guess MIME type
        mime_type = _guess_mime_type(path, entry_file)

        # 3. Write to DB using independent session
        from cubebox.db.engine import async_session_maker
        from cubebox.repositories import ArtifactRepository, ArtifactVersionRepository

        async with async_session_maker() as session:
            repo = ArtifactRepository(session)  # type: ignore[call-arg]
            version_repo = ArtifactVersionRepository(session)  # type: ignore[call-arg]

            # Auto-match: if no artifact_id given, look for an existing
            # artifact at the same path so we update instead of duplicating.
            if not artifact_id:
                existing = await repo.find_by_path(conversation_id, path)
                if existing:
                    artifact_id = existing.id
                    logger.info(
                        "Auto-matched artifact by path: id={}, path={}",
                        artifact_id,
                        path,
                    )

            if artifact_id:
                artifact = await repo.update(
                    artifact_id,
                    name=name,
                    artifact_type=artifact_type,
                    path=path,
                    entry_file=entry_file,
                    mime_type=mime_type,
                    description=description,
                )
                if not artifact:
                    return json.dumps({"error": f"Artifact not found: {artifact_id}"})
                action = "updated"
            else:
                artifact = await repo.create(
                    conversation_id=conversation_id,
                    name=name,
                    artifact_type=artifact_type,
                    path=path,
                    entry_file=entry_file,
                    mime_type=mime_type,
                    description=description,
                )
                action = "created"

            # Create version snapshot
            await version_repo.create(
                artifact_id=artifact.id,
                version=artifact.version,
                name=name,
                description=description,
                path=path,
                entry_file=entry_file,
                mime_type=mime_type,
            )

        # Upload to object storage (non-fatal on failure)
        try:
            from cubebox.objectstore import get_objectstore_client

            store = get_objectstore_client()
            key_prefix = f"artifacts/{conversation_id}/{artifact.id}/v{artifact.version}/"
            await store.upload_from_sandbox(sandbox, path, key_prefix)
        except Exception:
            logger.exception(
                "Failed to upload artifact {} to object storage (non-fatal)",
                artifact.id,
            )

        logger.info(
            "Artifact {}: id={}, name={}, type={}, version={}",
            action,
            artifact.id,
            artifact.name,
            artifact.artifact_type,
            artifact.version,
        )

        return json.dumps({"action": action, "artifact": artifact.to_dict()})

    return StructuredTool.from_function(
        coroutine=_save_artifact,
        name="save_artifact",
        description=(
            "Register a file or directory created in the sandbox as an artifact "
            "so the user can preview and download it. "
            "First create the files with the execute tool, then call this."
        ),
        args_schema=_SaveArtifactArgs,
    )


class ArtifactMiddleware(AgentMiddleware[Any, Any, Any]):
    """Registers save_artifact tool and injects artifact prompt into system message."""

    def __init__(self, *, sandbox: Sandbox, conversation_id: str) -> None:
        self.sandbox = sandbox
        self.conversation_id = conversation_id
        self.tools: Sequence[BaseTool] = [_create_save_artifact_tool(sandbox, conversation_id)]
        # Register content_type so stream.py can label tool results
        get_registry().register_content_type("save_artifact", "artifact")

    async def _build_artifact_list(self) -> str:
        """Query DB for existing artifacts and format as a prompt section."""
        from cubebox.db.engine import async_session_maker
        from cubebox.repositories import ArtifactRepository

        async with async_session_maker() as session:
            repo = ArtifactRepository(session)  # type: ignore[call-arg]
            artifacts = await repo.list_by_conversation(self.conversation_id)

        if not artifacts:
            return "\n**Existing artifacts:** None yet.\n"

        lines = ["\n**Existing artifacts:**"]
        for a in artifacts:
            lines.append(
                f'- id=`{a.id}` name="{a.name}" type={a.artifact_type} path=`{a.path}` v{a.version}'
            )
        return "\n".join(lines) + "\n"

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any] | AIMessage]],
    ) -> ModelResponse[Any] | AIMessage:
        artifact_list = await self._build_artifact_list()
        prompt = ARTIFACT_PROMPT + artifact_list
        new_system = append_to_system_message(request.system_message, prompt)
        return await handler(request.override(system_message=new_system))
