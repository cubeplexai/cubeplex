"""orgsettings nullable org_id surrogate pk

Revision ID: fef7470bc3a5
Revises: 4137c1986c3e
Create Date: 2026-06-09 20:23:03.532544

Refactor OrgSettings:
- add surrogate ``id`` PK
- make ``org_id`` nullable (FK preserved)
- replace the composite PK with two partial unique indexes
  (``uq_org_settings_org_key`` WHERE org_id IS NOT NULL,
   ``uq_org_settings_system_key`` WHERE org_id IS NULL)
- backfill existing rows with newly generated public IDs

Matches the system-row pattern from
``alembic/versions/d44dff875e38_vault_nullable_cred_org_id_provider_.py``.
"""

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel  # noqa: F401  (referenced by sqlmodel.sql.sqltypes.AutoString)
from alembic import op

from cubeplex.models.public_id import PREFIX_ORG_SETTING, generate_public_id


# revision identifiers, used by Alembic.
revision: str = "fef7470bc3a5"
down_revision: Union[str, Sequence[str], None] = "4137c1986c3e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. Add ``id`` column nullable so we can backfill.
    op.add_column(
        "org_settings",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(length=20), nullable=True),
    )
    # 2. Backfill existing rows with new public IDs.
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT org_id, key FROM org_settings")).fetchall()
    for r in rows:
        new_id = generate_public_id(PREFIX_ORG_SETTING)
        conn.execute(
            sa.text(
                "UPDATE org_settings SET id = :id "
                "WHERE org_id = :org_id AND key = :key"
            ),
            {"id": new_id, "org_id": r.org_id, "key": r.key},
        )
    # 3. id NOT NULL.
    op.alter_column(
        "org_settings", "id", existing_type=sa.VARCHAR(length=20), nullable=False
    )
    # 4. Drop the old composite PK.
    op.drop_constraint("org_settings_pkey", "org_settings", type_="primary")
    # 5. New PK on id.
    op.create_primary_key("org_settings_pkey", "org_settings", ["id"])
    # 6. Make org_id nullable (FK to organizations stays intact).
    op.alter_column(
        "org_settings",
        "org_id",
        existing_type=sa.VARCHAR(length=20),
        nullable=True,
    )
    # 7. Partial unique indexes.
    op.create_index(
        "uq_org_settings_org_key",
        "org_settings",
        ["org_id", "key"],
        unique=True,
        postgresql_where="org_id IS NOT NULL",
    )
    op.create_index(
        "uq_org_settings_system_key",
        "org_settings",
        ["key"],
        unique=True,
        postgresql_where="org_id IS NULL",
    )
    # 8. Index on org_id for FK joins.
    op.create_index(
        op.f("ix_org_settings_org_id"), "org_settings", ["org_id"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_org_settings_org_id"), table_name="org_settings")
    op.drop_index(
        "uq_org_settings_system_key",
        table_name="org_settings",
        postgresql_where="org_id IS NULL",
    )
    op.drop_index(
        "uq_org_settings_org_key",
        table_name="org_settings",
        postgresql_where="org_id IS NOT NULL",
    )
    op.alter_column(
        "org_settings",
        "org_id",
        existing_type=sa.VARCHAR(length=20),
        nullable=False,
    )
    op.drop_constraint("org_settings_pkey", "org_settings", type_="primary")
    op.create_primary_key("org_settings_pkey", "org_settings", ["org_id", "key"])
    op.drop_column("org_settings", "id")
