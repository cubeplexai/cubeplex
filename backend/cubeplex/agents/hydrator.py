"""AttachmentHydrator — sync ObjectStore attachments to sandbox before run."""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from cubeplex.objectstore.client import ObjectStoreClient
    from cubeplex.repositories import AttachmentRepository
    from cubeplex.sandbox.base import Sandbox


class AttachmentHydrationError(RuntimeError):
    """Raised when one or more attachments cannot be staged into the sandbox."""

    def __init__(self, *, file_id: str, cause: str) -> None:
        super().__init__(f"failed to hydrate attachment {file_id}: {cause}")
        self.file_id = file_id


class AttachmentHydrator:
    """Idempotent sync of attachment ObjectStore content into the sandbox FS."""

    def __init__(
        self,
        *,
        repo: AttachmentRepository,
        sandbox: Sandbox,
        objectstore: ObjectStoreClient,
    ) -> None:
        self.repo = repo
        self.sandbox = sandbox
        self.objectstore = objectstore

    async def hydrate(self, *, conversation_id: str, file_ids: list[str]) -> dict[str, str]:
        """Materialize each file_id into the sandbox if not already present.

        Returns: mapping {file_id -> sandbox_path}
        Raises:  AttachmentHydrationError on first failure (run should abort).
        """
        result: dict[str, str] = {}
        for fid in file_ids:
            row = await self.repo.get_in_conversation(
                conversation_id=conversation_id,
                attachment_id=fid,
            )
            if row is None:
                raise AttachmentHydrationError(file_id=fid, cause="row not found")

            # shlex.quote prevents shell metacharacters in the user-supplied
            # filename (e.g. ``$(whoami).xlsx``, backticks, ``;``) from being
            # evaluated by the sandbox shell. _safe_basename allows these
            # characters through, so the only safe form is single-quoting via
            # shlex — bash double-quotes still expand $() and backticks.
            quoted_path = shlex.quote(row.sandbox_path)
            check = await self.sandbox.execute(
                f"test -f {quoted_path} && echo EXISTS || echo MISSING"
            )
            if (check.output or "").strip() == "EXISTS":
                result[fid] = row.sandbox_path
                continue

            try:
                data, _ = await self.objectstore.download_file(row.object_key)
                await self.sandbox.upload([(row.sandbox_path, data)])
                result[fid] = row.sandbox_path
            except Exception as exc:  # noqa: BLE001 — re-raised wrapped
                logger.exception("hydrate failed for {}", fid)
                raise AttachmentHydrationError(file_id=fid, cause=str(exc)) from exc
        return result
