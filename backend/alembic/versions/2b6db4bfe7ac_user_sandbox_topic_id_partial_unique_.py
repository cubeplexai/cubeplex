"""user_sandbox topic_id + partial unique split

Revision ID: 2b6db4bfe7ac
Revises: 7b81f04dce1f
Create Date: 2026-06-18 00:49:34.756912

Adds nullable ``topic_id`` to ``user_sandboxes`` so a single sandbox row can
be keyed either by ``user_id`` (personal scope, existing behaviour) or by
``topic_id`` (dedicated group-chat scope where all participants share one
sandbox). Two partial unique indexes carve the active rows into disjoint
sets:

- ``uq_user_sandbox_active`` (re-created with a stricter predicate):
  active rows where ``topic_id IS NULL``, unique on ``(org_id, ws, user_id)``.
- ``uq_user_sandbox_active_topic`` (new): active rows where
  ``topic_id IS NOT NULL``, unique on ``(org_id, ws, topic_id)``.

The DROP + CREATE on the existing ``uq_user_sandbox_active`` is the most
important part of this migration: alembic's autogen does NOT detect
predicate-only changes on partial indexes, so without the hand-edit any
user with an already-running personal sandbox would hit IntegrityError on
their first dedicated-topic create — the OLD predicate (no ``topic_id``
filter) would still fire on the second row. Tests on a clean dev DB pass;
prod breaks on first contact with existing users.

``ondelete=SET NULL`` on the FK preserves the sandbox row (and the audit
trail of which user actually owned it) when the referenced topic is hard-
deleted; the row then drains via its normal status-based GC path.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '2b6db4bfe7ac'
down_revision: Union[str, Sequence[str], None] = '7b81f04dce1f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "user_sandboxes",
        sa.Column("topic_id", sa.String(length=20), nullable=True),
    )
    op.create_foreign_key(
        "fk_user_sandboxes_topic_id",
        "user_sandboxes",
        "topics",
        ["topic_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_user_sandboxes_topic_id",
        "user_sandboxes",
        ["topic_id"],
    )

    # CRITICAL: drop and recreate the existing partial unique with the new
    # predicate (``topic_id IS NULL AND ...``). Autogen does NOT detect
    # predicate-only changes on partial indexes — without this, dedicated
    # mode breaks for any user with a pre-existing personal sandbox row.
    op.drop_index("uq_user_sandbox_active", table_name="user_sandboxes")
    op.create_index(
        "uq_user_sandbox_active",
        "user_sandboxes",
        ["org_id", "workspace_id", "user_id"],
        unique=True,
        postgresql_where=sa.text(
            "topic_id IS NULL AND status IN ('provisioning','running')"
        ),
    )
    op.create_index(
        "uq_user_sandbox_active_topic",
        "user_sandboxes",
        ["org_id", "workspace_id", "topic_id"],
        unique=True,
        postgresql_where=sa.text(
            "topic_id IS NOT NULL AND status IN ('provisioning','running')"
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_user_sandbox_active_topic", table_name="user_sandboxes")
    op.drop_index("uq_user_sandbox_active", table_name="user_sandboxes")
    op.create_index(
        "uq_user_sandbox_active",
        "user_sandboxes",
        ["org_id", "workspace_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('provisioning','running')"),
    )
    op.drop_index("ix_user_sandboxes_topic_id", table_name="user_sandboxes")
    op.drop_constraint(
        "fk_user_sandboxes_topic_id", "user_sandboxes", type_="foreignkey"
    )
    op.drop_column("user_sandboxes", "topic_id")
