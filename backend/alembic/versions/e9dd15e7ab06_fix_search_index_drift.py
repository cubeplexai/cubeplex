"""fix_search_index_drift

Backfills three pieces of schema drift introduced by the hand-written
``fabe1279b9f6_conversation_search_tables`` migration:

* ``OrgScopedMixin`` declares ``org_id`` / ``workspace_id`` with
  ``index=True``, but the hand-written CREATE TABLE skipped those
  per-column indexes. Add them on ``conversation_chunks``,
  ``embedding_jobs`` and ``search_backfill_progress``.
* ``EmbeddingJob.state`` is typed ``EmbeddingJobState`` (StrEnum) in the
  model, but the column was created as ``VARCHAR(10)``. Convert it to a
  proper ``embeddingjobstate`` PG enum so the DB enforces the value set
  and future autogen runs stop reporting drift.

Revision ID: e9dd15e7ab06
Revises: 59fd2d03ce79
Create Date: 2026-06-19 00:15:45.816874

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e9dd15e7ab06'
down_revision: Union[str, Sequence[str], None] = '59fd2d03ce79'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_EMBEDDING_JOB_STATE = sa.Enum(
    'pending', 'running', 'done', 'dead', name='embeddingjobstate'
)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        op.f('ix_conversation_chunks_org_id'),
        'conversation_chunks', ['org_id'], unique=False,
    )
    op.create_index(
        op.f('ix_conversation_chunks_workspace_id'),
        'conversation_chunks', ['workspace_id'], unique=False,
    )
    op.create_index(
        op.f('ix_embedding_jobs_org_id'),
        'embedding_jobs', ['org_id'], unique=False,
    )
    op.create_index(
        op.f('ix_embedding_jobs_workspace_id'),
        'embedding_jobs', ['workspace_id'], unique=False,
    )
    op.create_index(
        op.f('ix_search_backfill_progress_org_id'),
        'search_backfill_progress', ['org_id'], unique=False,
    )
    op.create_index(
        op.f('ix_search_backfill_progress_workspace_id'),
        'search_backfill_progress', ['workspace_id'], unique=False,
    )

    # Drop the old VARCHAR server_default first — PG can't auto-cast a
    # ``character varying`` default expression when the column type
    # changes to the new enum.
    op.execute("ALTER TABLE embedding_jobs ALTER COLUMN state DROP DEFAULT")
    _EMBEDDING_JOB_STATE.create(op.get_bind(), checkfirst=True)
    op.alter_column(
        'embedding_jobs', 'state',
        existing_type=sa.VARCHAR(length=10),
        type_=_EMBEDDING_JOB_STATE,
        existing_nullable=False,
        postgresql_using='state::text::embeddingjobstate',
    )
    op.execute(
        "ALTER TABLE embedding_jobs ALTER COLUMN state SET DEFAULT 'pending'"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("ALTER TABLE embedding_jobs ALTER COLUMN state DROP DEFAULT")
    op.alter_column(
        'embedding_jobs', 'state',
        existing_type=_EMBEDDING_JOB_STATE,
        type_=sa.VARCHAR(length=10),
        existing_nullable=False,
        postgresql_using='state::text',
    )
    op.execute(
        "ALTER TABLE embedding_jobs ALTER COLUMN state "
        "SET DEFAULT 'pending'::character varying"
    )
    _EMBEDDING_JOB_STATE.drop(op.get_bind(), checkfirst=True)

    op.drop_index(
        op.f('ix_search_backfill_progress_workspace_id'),
        table_name='search_backfill_progress',
    )
    op.drop_index(
        op.f('ix_search_backfill_progress_org_id'),
        table_name='search_backfill_progress',
    )
    op.drop_index(op.f('ix_embedding_jobs_workspace_id'), table_name='embedding_jobs')
    op.drop_index(op.f('ix_embedding_jobs_org_id'), table_name='embedding_jobs')
    op.drop_index(
        op.f('ix_conversation_chunks_workspace_id'),
        table_name='conversation_chunks',
    )
    op.drop_index(
        op.f('ix_conversation_chunks_org_id'),
        table_name='conversation_chunks',
    )
