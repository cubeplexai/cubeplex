"""Topic repository — scoped by workspace, filtered by participant membership."""

from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models.topic import Topic, TopicParticipant
from cubebox.repositories.base import ScopedRepository


class TopicRepository(ScopedRepository[Topic]):
    model = Topic

    def __init__(
        self,
        session: AsyncSession,
        *,
        org_id: str,
        workspace_id: str,
        user_id: str,
    ) -> None:
        super().__init__(session, org_id=org_id, workspace_id=workspace_id)
        self.user_id = user_id

    def _scoped_select(self) -> Any:
        return (
            super()
            ._scoped_select()
            .where(
                cast(Any, Topic.id).in_(
                    select(cast(Any, TopicParticipant.topic_id)).where(
                        TopicParticipant.user_id == self.user_id  # type: ignore[arg-type]
                    )
                ),
                cast(Any, Topic.is_archived).is_(False),
            )
        )

    async def create_topic(
        self,
        *,
        title: str,
        sandbox_mode: str | None = None,
        max_participants: int = 20,
    ) -> Topic:
        topic = Topic(
            org_id=self.org_id,
            workspace_id=self.workspace_id,
            creator_user_id=self.user_id,
            title=title,
            sandbox_mode=sandbox_mode,
            max_participants=max_participants,
        )
        self.session.add(topic)
        await self.session.flush()

        owner = TopicParticipant(
            topic_id=topic.id,
            user_id=self.user_id,
            role="owner",
        )
        self.session.add(owner)
        await self.session.flush()
        return topic

    async def add_participants(
        self,
        topic_id: str,
        user_ids: list[str],
    ) -> list[TopicParticipant]:
        topic = await self.get(topic_id)
        if topic is None:
            raise ValueError(f"Topic {topic_id} not found")

        # Deduplicate the input — a caller passing [uid_a, uid_a] would
        # otherwise pass the cap check and then flush would raise on
        # uq_topic_participant. Preserve insertion order.
        unique_ids: list[str] = []
        seen: set[str] = set()
        for uid in user_ids:
            if uid not in seen:
                seen.add(uid)
                unique_ids.append(uid)

        # Lock the topic row FIRST so concurrent invites for the same
        # user_id serialize. If the dedup query ran before the lock, two
        # concurrent invites of the same user would each see no existing
        # row and both attempt the INSERT — the loser raises IntegrityError
        # on uq_topic_participant.
        lock_stmt = (
            select(Topic)
            .where(Topic.id == topic_id)  # type: ignore[arg-type]
            .with_for_update()
        )
        await self.session.execute(lock_stmt)

        # Skip user_ids who are already participants (idempotent add).
        existing_stmt = select(cast(Any, TopicParticipant.user_id)).where(
            TopicParticipant.topic_id == topic_id,  # type: ignore[arg-type]
            cast(Any, TopicParticipant.user_id).in_(unique_ids),
        )
        existing_result = await self.session.execute(existing_stmt)
        already_member = set(existing_result.scalars().all())
        to_add = [uid for uid in unique_ids if uid not in already_member]

        count_stmt = select(func.count()).where(
            TopicParticipant.topic_id == topic_id  # type: ignore[arg-type]
        )
        result = await self.session.execute(count_stmt)
        current_count = result.scalar_one()

        # current_count already includes the owner; the cap is the total
        # participant count (creator counts toward max_participants).
        if current_count + len(to_add) > topic.max_participants:
            raise ValueError(
                f"Adding {len(to_add)} would exceed max {topic.max_participants} "
                f"(current: {current_count})"
            )

        from cubebox.repositories import MembershipRepository

        membership_repo = MembershipRepository(self.session)
        for uid in to_add:
            role = await membership_repo.get_role(user_id=uid, workspace_id=self.workspace_id)
            if role is None:
                raise ValueError(f"User {uid} is not a member of this workspace")

        participants: list[TopicParticipant] = []
        for uid in to_add:
            p = TopicParticipant(topic_id=topic_id, user_id=uid, role="member")
            self.session.add(p)
            participants.append(p)
        await self.session.flush()
        return participants

    async def remove_participant(self, topic_id: str, user_id: str) -> None:
        stmt = select(TopicParticipant).where(
            TopicParticipant.topic_id == topic_id,  # type: ignore[arg-type]
            TopicParticipant.user_id == user_id,  # type: ignore[arg-type]
        )
        result = await self.session.execute(stmt)
        participant = result.scalar_one_or_none()
        if participant is None:
            raise ValueError(f"User {user_id} is not a participant of topic {topic_id}")

        if participant.role == "owner":
            others_stmt = (
                select(TopicParticipant)
                .where(
                    TopicParticipant.topic_id == topic_id,  # type: ignore[arg-type]
                    TopicParticipant.user_id != user_id,  # type: ignore[arg-type]
                )
                .order_by(cast(Any, TopicParticipant.joined_at))
                .limit(1)
            )
            others = await self.session.execute(others_stmt)
            next_owner = others.scalar_one_or_none()
            if next_owner is not None:
                next_owner.role = "owner"
                self.session.add(next_owner)

        await self.session.delete(participant)
        await self.session.flush()

    async def list_participants(self, topic_id: str) -> list[TopicParticipant]:
        stmt = (
            select(TopicParticipant)
            .where(TopicParticipant.topic_id == topic_id)  # type: ignore[arg-type]
            .order_by(cast(Any, TopicParticipant.joined_at))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_participant(self, topic_id: str, user_id: str) -> TopicParticipant | None:
        stmt = select(TopicParticipant).where(
            TopicParticipant.topic_id == topic_id,  # type: ignore[arg-type]
            TopicParticipant.user_id == user_id,  # type: ignore[arg-type]
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def is_participant(self, topic_id: str, user_id: str) -> bool:
        return await self.get_participant(topic_id, user_id) is not None

    async def archive(self, topic_id: str) -> None:
        topic = await self.get(topic_id)
        if topic is None:
            raise ValueError(f"Topic {topic_id} not found")
        topic.is_archived = True
        self.session.add(topic)
        await self.session.flush()

    async def get_with_participants(
        self, topic_id: str
    ) -> tuple[Topic | None, list[TopicParticipant]]:
        topic = await self.get(topic_id)
        if topic is None:
            return None, []
        participants = await self.list_participants(topic_id)
        return topic, participants

    async def list_for_sidebar(self) -> list[Topic]:
        """Sidebar order: most recent activity first."""
        stmt = self._scoped_select().order_by(cast(Any, Topic.last_activity_at).desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def participant_counts(self, topic_ids: list[str]) -> dict[str, int]:
        """Return ``{topic_id: count}`` for the given topic_ids in one query.

        Used by the sidebar list endpoint so the badge can render the
        right member count without an N+1 round-trip per topic.
        """
        if not topic_ids:
            return {}
        stmt = (
            select(
                cast(Any, TopicParticipant.topic_id),
                func.count(),
            )
            .where(cast(Any, TopicParticipant.topic_id).in_(topic_ids))
            .group_by(cast(Any, TopicParticipant.topic_id))
        )
        rows = (await self.session.execute(stmt)).all()
        out: dict[str, int] = dict.fromkeys(topic_ids, 0)
        for topic_id, count in rows:
            out[str(topic_id)] = int(count)
        return out

    async def bump_activity(self, topic_id: str) -> None:
        """Update ``last_activity_at`` to now. Called from the message
        insertion path; safe to call on a topic the caller may not be
        a participant of (system path).

        Scoped to ``(org_id, workspace_id)`` for defense-in-depth: a
        spoofed/misrouted ``topic_id`` cannot touch a row in a different
        workspace. Monotonic: only bumps forward, so a late-arriving
        message under clock skew cannot reorder the sidebar backward.
        """
        now = datetime.now(UTC)
        stmt = (
            update(Topic)
            .where(
                cast(Any, Topic.id) == topic_id,
                cast(Any, Topic.org_id) == self.org_id,
                cast(Any, Topic.workspace_id) == self.workspace_id,
                cast(Any, Topic.last_activity_at) < now,
            )
            .values(last_activity_at=now)
        )
        await self.session.execute(stmt)
