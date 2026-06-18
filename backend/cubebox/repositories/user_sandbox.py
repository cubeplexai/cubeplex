"""UserSandbox repository."""

from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models.user_sandbox import UserSandbox
from cubebox.repositories.base import ScopedRepository


class UserSandboxRepository(ScopedRepository[UserSandbox]):
    """Repository for UserSandbox CRUD operations."""

    model = UserSandbox

    _ACTIVE_STATUSES = ("provisioning", "running")

    async def create(
        self,
        *,
        user_id: str,
        sandbox_id: str,
        image: str,
        volumes_config: dict[str, Any] | None = None,
        ttl_seconds: int = 3600,
        paused_ttl_seconds: int | None = None,
    ) -> UserSandbox:
        """Create a new user sandbox record.

        ``paused_ttl_seconds`` overrides the model default when set; the manager
        passes its configured value so ``sandbox.paused_ttl_seconds`` actually
        drives ``reap_paused`` instead of always taking the model default.
        """
        fields: dict[str, Any] = {
            "user_id": user_id,
            "sandbox_id": sandbox_id,
            "image": image,
            "volumes_config": volumes_config,
            "ttl_seconds": ttl_seconds,
        }
        if paused_ttl_seconds is not None:
            fields["paused_ttl_seconds"] = paused_ttl_seconds
        return await self.add(UserSandbox(**fields))

    async def reserve(
        self,
        *,
        user_id: str,
        image: str,
        scope_type: str,
        scope_id: str,
        volumes_config: dict[str, Any] | None = None,
        ttl_seconds: int = 3600,
    ) -> UserSandbox:
        """Insert a provisioning placeholder row BEFORE provider create.

        The partial unique index over ('provisioning','running') makes a
        concurrent second reserve for the same scope key raise an
        IntegrityError, so the loser never provisions a provider sandbox.
        ``sandbox_id`` gets a unique ``pending-<row id>`` placeholder until
        promote overwrites it.

        ``scope_type`` / ``scope_id`` is the polymorphic key: ``'user' +
        user_id`` for personal sandboxes, ``'conversation' + conv_id`` for
        standalone group chats, ``'topic' + topic_id`` for dedicated topic
        sandboxes. The key feeds into the single
        ``uq_user_sandbox_active_scope`` partial unique, ensuring at most
        one active row per scope tuple.
        """
        record = UserSandbox(
            user_id=user_id,
            sandbox_id="",  # set below once the row id is minted
            status="provisioning",
            image=image,
            volumes_config=volumes_config,
            ttl_seconds=ttl_seconds,
            scope_type=scope_type,
            scope_id=scope_id,
        )
        record.sandbox_id = f"pending-{record.id}"
        return await self.add(record)

    async def promote_to_running(self, record_id: str, *, sandbox_id: str) -> None:
        record = await self.get(record_id)
        if record is None:
            raise ValueError(f"sandbox record {record_id} vanished mid-create")
        record.sandbox_id = sandbox_id
        record.status = "running"
        await self.session.commit()

    async def delete_record(self, record_id: str) -> None:
        await self.delete(record_id)

    async def get_active_by_scope(self, *, scope_type: str, scope_id: str) -> UserSandbox | None:
        """Return the active (provisioning OR running) sandbox for this scope.

        ``uq_user_sandbox_active_scope`` guarantees at most one matching
        row per (org, ws, scope_type, scope_id), so no ``order_by/limit``
        "newest wins" is needed.
        """
        stmt = (
            self._scoped_select()
            .where(UserSandbox.scope_type == scope_type)
            .where(UserSandbox.scope_id == scope_id)
            .where(UserSandbox.status.in_(self._ACTIVE_STATUSES))  # type: ignore[attr-defined]
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_resumable_by_scope(self, *, scope_type: str, scope_id: str) -> UserSandbox | None:
        """Return any non-terminal row for this scope (running/paused/
        pausing/resuming).

        Transient rows (``pausing`` / ``resuming``) ARE returned so a late
        ``get_or_create`` caller sees the in-flight lifecycle row instead of
        treating it as absent and provisioning a duplicate sandbox. The
        manager waits on transients to reach a stable state (paused/running/
        failed/terminated) before acting.
        """
        stmt = (
            self._scoped_select()
            .where(UserSandbox.scope_type == scope_type)
            .where(UserSandbox.scope_id == scope_id)
            .where(UserSandbox.status.in_(("running", "paused", "pausing", "resuming")))  # type: ignore[attr-defined]
            .order_by(UserSandbox.created_at.desc())  # type: ignore[attr-defined]
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def rekey_to_topic(
        self,
        *,
        creator_user_id: str,
        conversation_id: str,
        topic_id: str,
    ) -> None:
        """Re-scope the active sandbox row for an upgrade-to-topic in one shot.

        Prefers the conversation-scope row when present (more recent state
        from a prior group-chat upgrade), and falls back to the personal
        user-scope row (1:1 case). The two-step UPDATE closes the race where
        ``upgrade_conversation_to_topic`` reads ``is_group_chat=False`` then
        a concurrent ``invite-to-group`` flips the sandbox to conversation-
        scope before the upgrade's rekey runs.

        At most one row gets rekeyed. A single OR-matched UPDATE could touch
        BOTH rows (e.g. paused user-scope + running conversation-scope),
        producing two ``(topic, topic_id)`` rows — the partial unique index
        only covers active states, so the collision surfaces later when both
        try to resume.
        """
        conv_stmt = (
            update(UserSandbox)
            .where(
                UserSandbox.org_id == self.org_id,  # type: ignore[arg-type]
                UserSandbox.workspace_id == self.workspace_id,  # type: ignore[arg-type]
                UserSandbox.scope_type == "conversation",  # type: ignore[arg-type]
                UserSandbox.scope_id == conversation_id,  # type: ignore[arg-type]
                UserSandbox.status.in_(  # type: ignore[attr-defined]
                    ("provisioning", "running", "paused", "resuming")
                ),
            )
            .values(scope_type="topic", scope_id=topic_id)
        )
        res = cast(CursorResult[Any], await self.session.execute(conv_stmt))
        if res.rowcount > 0:
            return

        user_stmt = (
            update(UserSandbox)
            .where(
                UserSandbox.org_id == self.org_id,  # type: ignore[arg-type]
                UserSandbox.workspace_id == self.workspace_id,  # type: ignore[arg-type]
                UserSandbox.scope_type == "user",  # type: ignore[arg-type]
                UserSandbox.scope_id == creator_user_id,  # type: ignore[arg-type]
                UserSandbox.status.in_(  # type: ignore[attr-defined]
                    ("provisioning", "running", "paused", "resuming")
                ),
            )
            .values(scope_type="topic", scope_id=topic_id)
        )
        await self.session.execute(user_stmt)

    async def rekey(
        self,
        *,
        from_scope_type: str,
        from_scope_id: str,
        to_scope_type: str,
        to_scope_id: str,
    ) -> None:
        """Re-scope the active sandbox row in place.

        Used by the upgrade endpoints: when a 1:1 becomes a standalone
        group chat (user -> conversation) or when a standalone group chat
        becomes a topic (conversation -> topic), the same running sandbox
        is inherited under the new scope key. One UPDATE, no file
        movement.
        """
        stmt = (
            update(UserSandbox)
            .where(
                UserSandbox.org_id == self.org_id,  # type: ignore[arg-type]
                UserSandbox.workspace_id == self.workspace_id,  # type: ignore[arg-type]
                UserSandbox.scope_type == from_scope_type,  # type: ignore[arg-type]
                UserSandbox.scope_id == from_scope_id,  # type: ignore[arg-type]
                UserSandbox.status.in_(  # type: ignore[attr-defined]
                    ("provisioning", "running", "paused", "resuming")
                ),
            )
            .values(scope_type=to_scope_type, scope_id=to_scope_id)
        )
        await self.session.execute(stmt)

    async def get_by_sandbox_id(self, sandbox_id: str) -> UserSandbox | None:
        """Get record by OpenSandbox sandbox ID."""
        stmt = self._scoped_select().where(UserSandbox.sandbox_id == sandbox_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def update_activity(self, record_id: str) -> None:
        """Update last_activity_at timestamp."""
        record = await self.get(record_id)
        if record:
            record.last_activity_at = datetime.now(UTC)
            await self.session.commit()

    async def update_activity_by_sandbox_id(self, sandbox_id: str) -> None:
        """Update last_activity_at by OpenSandbox sandbox ID."""
        record = await self.get_by_sandbox_id(sandbox_id)
        if record:
            record.last_activity_at = datetime.now(UTC)
            await self.session.commit()

    async def mark_terminated(self, record_id: str) -> None:
        """Mark a sandbox as terminated."""
        record = await self.get(record_id)
        if record:
            record.status = "terminated"
            await self.session.commit()

    async def mark_failed_from_transient(self, record_id: str) -> bool:
        """Atomically flip ``pausing``/``resuming`` -> ``failed``.

        Used by the reconciler when ``get_info`` returns ``state=Failed``:
        the unguarded ``mark_failed`` would clobber a row that was just
        transitioned to ``running`` by a concurrent ``_resume_record``
        (the reconciler's view of provider state can lag the DB by ms).
        The prior-state guard rejects that race so concurrent successful
        resumes are never overwritten.
        """
        stmt = (
            update(UserSandbox)
            .where(
                UserSandbox.id == record_id,  # type: ignore[arg-type]
                UserSandbox.org_id == self.org_id,  # type: ignore[arg-type]
                UserSandbox.workspace_id == self.workspace_id,  # type: ignore[arg-type]
                UserSandbox.status.in_(("pausing", "resuming")),  # type: ignore[attr-defined]
            )
            .values(status="failed")
        )
        result = cast(CursorResult[Any], await self.session.execute(stmt))
        await self.session.commit()
        return bool(result.rowcount == 1)

    async def mark_failed_from_resuming(self, record_id: str) -> bool:
        """Atomically flip ``resuming -> failed``.

        Kept for callers that want the narrower ``resuming``-only guard
        (currently unused; reserved). Production paths use the broader
        :meth:`mark_failed_from_transient` which accepts ``pausing`` too.
        """
        return await self._transition(record_id, "resuming", "failed")

    async def claim_terminated_from_paused(
        self, record_id: str, *, paused_ttl_seconds: int
    ) -> bool:
        """Atomically flip paused -> terminated for an expired paused row.

        Used by ``reap_paused`` so a concurrent ``_resume_record`` taking the
        same row through ``paused -> resuming`` doesn't get killed under its
        feet. The reaper calls the provider ``kill()`` only on a successful
        claim. The TTL re-assertion in the WHERE clause prevents a row that
        was just refreshed (e.g. via the reconciler bumping ``paused_at``)
        from being killed.
        """
        stmt = (
            update(UserSandbox)
            .where(
                UserSandbox.id == record_id,  # type: ignore[arg-type]
                UserSandbox.org_id == self.org_id,  # type: ignore[arg-type]
                UserSandbox.workspace_id == self.workspace_id,  # type: ignore[arg-type]
                UserSandbox.status == "paused",  # type: ignore[arg-type]
                UserSandbox.paused_at.is_not(None),  # type: ignore[union-attr]
                text("paused_at + :ttl * INTERVAL '1 second' <= NOW()").bindparams(
                    ttl=paused_ttl_seconds
                ),
            )
            .values(status="terminated")
        )
        result = cast(CursorResult[Any], await self.session.execute(stmt))
        await self.session.commit()
        return bool(result.rowcount == 1)

    async def claim_pausing(self, record_id: str, *, idle_ttl_seconds: int) -> bool:
        """Atomically flip running -> pausing, re-asserting idleness + lease.

        A single conditional UPDATE: the idleness, status, and lease checks
        live in the WHERE clause so a fresh touch landing between selection
        and claim makes the claim a no-op. Returns whether a row was claimed.

        ``idle_ttl_seconds`` is the manager-configured pause idle TTL — used
        instead of each row's ``ttl_seconds`` (which controls the kill path)
        so operators can tune pause cadence via ``sandbox.idle_ttl_seconds``.
        """
        stmt = (
            update(UserSandbox)
            .where(
                UserSandbox.id == record_id,  # type: ignore[arg-type]
                UserSandbox.org_id == self.org_id,  # type: ignore[arg-type]
                UserSandbox.workspace_id == self.workspace_id,  # type: ignore[arg-type]
                UserSandbox.status == "running",  # type: ignore[arg-type]
                text(
                    "(in_use_until IS NULL OR in_use_until < NOW()) "
                    "AND last_activity_at + :idle_ttl * INTERVAL '1 second' <= NOW()"
                ).bindparams(idle_ttl=idle_ttl_seconds),
            )
            .values(status="pausing")
        )
        result = cast(CursorResult[Any], await self.session.execute(stmt))
        await self.session.commit()
        return bool(result.rowcount == 1)

    async def _transition(self, record_id: str, frm: str, to: str, **extra: Any) -> bool:
        stmt = (
            update(UserSandbox)
            .where(
                UserSandbox.id == record_id,  # type: ignore[arg-type]
                UserSandbox.org_id == self.org_id,  # type: ignore[arg-type]
                UserSandbox.workspace_id == self.workspace_id,  # type: ignore[arg-type]
                UserSandbox.status == frm,  # type: ignore[arg-type]
            )
            .values(status=to, **extra)
        )
        result = cast(CursorResult[Any], await self.session.execute(stmt))
        await self.session.commit()
        return bool(result.rowcount == 1)

    async def mark_paused(self, record_id: str, *, paused_at: datetime | None = None) -> bool:
        """Move to ``paused`` from ``pausing`` (pause succeeded) OR ``resuming``
        (resume aborted mid-flight and provider still reports ``Paused``).
        Stamps ``paused_at``.
        """
        stmt = (
            update(UserSandbox)
            .where(
                UserSandbox.id == record_id,  # type: ignore[arg-type]
                UserSandbox.org_id == self.org_id,  # type: ignore[arg-type]
                UserSandbox.workspace_id == self.workspace_id,  # type: ignore[arg-type]
                UserSandbox.status.in_(("pausing", "resuming")),  # type: ignore[attr-defined]
            )
            .values(status="paused", paused_at=paused_at or datetime.now(UTC))
        )
        result = cast(CursorResult[Any], await self.session.execute(stmt))
        await self.session.commit()
        return bool(result.rowcount == 1)

    async def mark_resuming(self, record_id: str) -> bool:
        """Move ``paused`` -> ``resuming``."""
        return await self._transition(record_id, "paused", "resuming")

    async def mark_running(
        self, record_id: str, *, last_resumed_at: datetime | None = None
    ) -> bool:
        """Move to ``running`` from either ``pausing`` (pause failed -> revert)
        or ``resuming`` (resume completed)."""
        extra: dict[str, Any] = {}
        if last_resumed_at is not None:
            extra["last_resumed_at"] = last_resumed_at
        stmt = (
            update(UserSandbox)
            .where(
                UserSandbox.id == record_id,  # type: ignore[arg-type]
                UserSandbox.org_id == self.org_id,  # type: ignore[arg-type]
                UserSandbox.workspace_id == self.workspace_id,  # type: ignore[arg-type]
                UserSandbox.status.in_(("pausing", "resuming")),  # type: ignore[attr-defined]
            )
            .values(status="running", **extra)
        )
        result = cast(CursorResult[Any], await self.session.execute(stmt))
        await self.session.commit()
        return bool(result.rowcount == 1)

    async def mark_failed(self, record_id: str) -> None:
        """Mark a sandbox as failed (terminal)."""
        record = await self.get(record_id)
        if record:
            record.status = "failed"
            await self.session.commit()

    async def acquire_in_use(self, record_id: str, lease_seconds: int) -> None:
        """Set ``in_use_until`` to now+lease_seconds, blocking auto-pause."""
        record = await self.get(record_id)
        if record:
            record.in_use_until = datetime.now(UTC) + timedelta(seconds=lease_seconds)
            await self.session.commit()

    async def release_in_use(self, record_id: str) -> None:
        """Clear the in-use lease."""
        record = await self.get(record_id)
        if record:
            record.in_use_until = None
            await self.session.commit()

    async def list_expired(self) -> list[UserSandbox]:
        """List sandboxes that have exceeded their TTL since last activity.

        Sweeps both ``running`` and ``provisioning`` rows so a crash mid-create
        cannot orphan a reserved slot past its TTL.
        """
        stmt = (
            self._scoped_select()
            .where(UserSandbox.status.in_(self._ACTIVE_STATUSES))  # type: ignore[attr-defined]
            .where(text("last_activity_at + ttl_seconds * INTERVAL '1 second' < NOW()"))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    async def list_expired_system(cls, session: AsyncSession) -> list[UserSandbox]:
        """System-scope query: find expired sandboxes across all workspaces.

        Only for background reapers — never expose to user-facing code. Sweeps
        ``provisioning`` rows too so a crashed reserve can't pin the partial
        unique slot forever.
        """
        stmt = (
            select(UserSandbox)
            .where(UserSandbox.status.in_(cls._ACTIVE_STATUSES))  # type: ignore[attr-defined]
            .where(text("last_activity_at + ttl_seconds * INTERVAL '1 second' < NOW()"))
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    async def list_idle_to_pause_system(
        cls, session: AsyncSession, *, idle_ttl_seconds: int
    ) -> list[UserSandbox]:
        """System-scope query: stale-idle, unleased ``running`` rows.

        Used by the pause reaper to pick candidates before claiming each
        atomically via ``claim_pausing``. The same ``idle_ttl_seconds`` must
        be passed to ``claim_pausing`` so the WHERE-clause re-assertion is
        consistent.
        """
        stmt = (
            select(UserSandbox)
            .where(UserSandbox.status == "running")  # type: ignore[arg-type]
            .where(
                text("last_activity_at + :idle_ttl * INTERVAL '1 second' <= NOW()").bindparams(
                    idle_ttl=idle_ttl_seconds
                )
            )
            .where(text("(in_use_until IS NULL OR in_use_until < NOW())"))
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    async def list_transient_for_reconcile_system(
        cls,
        session: AsyncSession,
        *,
        claim_timeout: int = 60,
    ) -> list[UserSandbox]:
        """System-scope query: ``pausing``/``resuming`` rows due for a provider
        recheck. ``last_provider_check`` NULL or older than ``claim_timeout``
        seconds qualifies; the reconciler will then read ``get_info()`` and
        repair the row.
        """
        stmt = (
            select(UserSandbox)
            .where(UserSandbox.status.in_(("pausing", "resuming")))  # type: ignore[attr-defined]
            .where(
                # Parenthesise the disjunction so SQL's ``AND > OR`` precedence
                # doesn't slip the OR branch past the status filter — without
                # the parens this would also match any non-transient row with
                # a stale ``last_provider_check``.
                text(
                    "(last_provider_check IS NULL "
                    "OR last_provider_check + :ct * INTERVAL '1 second' <= NOW())"
                )
            )
            .params(ct=claim_timeout)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def touch_provider_check(self, record_id: str) -> None:
        """Stamp ``last_provider_check`` to now after a reconcile-loop probe."""
        stmt = (
            update(UserSandbox)
            .where(
                UserSandbox.id == record_id,  # type: ignore[arg-type]
                UserSandbox.org_id == self.org_id,  # type: ignore[arg-type]
                UserSandbox.workspace_id == self.workspace_id,  # type: ignore[arg-type]
            )
            .values(last_provider_check=datetime.now(UTC))
        )
        await self.session.execute(stmt)
        await self.session.commit()

    @classmethod
    async def list_paused_expired_system(cls, session: AsyncSession) -> list[UserSandbox]:
        """System-scope query: ``paused`` rows past their paused-TTL.

        Used by the reap-paused background loop to terminate stale paused
        sandboxes.
        """
        stmt = (
            select(UserSandbox)
            .where(UserSandbox.status == "paused")  # type: ignore[arg-type]
            .where(UserSandbox.paused_at.is_not(None))  # type: ignore[union-attr]
            .where(text("paused_at + paused_ttl_seconds * INTERVAL '1 second' <= NOW()"))
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())
