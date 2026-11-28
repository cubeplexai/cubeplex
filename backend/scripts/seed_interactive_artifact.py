"""Seed a website artifact whose HTML calls the artifact bridge.

The browser E2E suite needs an artifact to preview, and artifacts are only
created by the agent (``save_artifact``) — there is no HTTP create route. This
script writes the rows and object-store bytes directly so the Playwright suite
can exercise the panel + bridge without running a model.

Print JSON ``{"conversation_id", "artifact_id", "workspace_id"}`` on success.

Usage (workspace/user ids come from the logged-in Playwright session)::

    uv run python scripts/dev/seed_interactive_artifact.py \
        --workspace-id ws-... --user-id usr-... --html-file /tmp/artifact.html
"""

from __future__ import annotations

import argparse
import asyncio
import json
import mimetypes
import sys
from pathlib import Path

from sqlalchemy import select

from cubeplex.db.engine import async_session_maker
from cubeplex.models import Artifact, ArtifactVersion, Conversation, Workspace
from cubeplex.objectstore import get_objectstore_client
from cubeplex.objectstore.artifact_paths import artifact_file_key

ENTRY_FILE = "index.html"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--html-file", required=True, type=Path)
    parser.add_argument("--name", default="Interactive artifact")
    parser.add_argument("--title", default="Interactive artifact")
    return parser.parse_args(argv)


async def _seed(args: argparse.Namespace, html: bytes) -> dict[str, str]:
    async with async_session_maker() as session:
        workspace = (
            await session.execute(select(Workspace).where(Workspace.id == args.workspace_id))
        ).scalar_one_or_none()
        if workspace is None:
            raise SystemExit(f"workspace {args.workspace_id} not found")
        org_id = workspace.org_id

        conversation = Conversation(
            org_id=org_id,
            workspace_id=args.workspace_id,
            creator_user_id=args.user_id,
            title=args.title,
        )
        session.add(conversation)
        # Flush so the FK row exists: these models have no ORM relationship, so
        # the unit of work may otherwise order the artifact insert first.
        await session.flush()

        artifact = Artifact(
            org_id=org_id,
            workspace_id=args.workspace_id,
            conversation_id=conversation.id,
            name=args.name,
            artifact_type="website",
            path=ENTRY_FILE,
            entry_file=ENTRY_FILE,
            mime_type=mimetypes.guess_type(ENTRY_FILE)[0] or "text/html",
            description="Seeded for the artifact bridge browser test",
        )
        session.add(artifact)
        session.add(
            ArtifactVersion(
                org_id=org_id,
                workspace_id=args.workspace_id,
                artifact_id=artifact.id,
                version=artifact.version,
                name=artifact.name,
                description=artifact.description,
                path=artifact.path,
                entry_file=artifact.entry_file,
                mime_type=artifact.mime_type,
            )
        )
        await session.commit()

        store = get_objectstore_client()
        await store.upload_file(
            artifact_file_key(artifact.id, artifact.version, ENTRY_FILE),
            html,
            content_type="text/html; charset=utf-8",
        )

        return {
            "conversation_id": conversation.id,
            "artifact_id": artifact.id,
            "workspace_id": args.workspace_id,
        }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    html = args.html_file.read_bytes()
    print(json.dumps(asyncio.run(_seed(args, html))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
