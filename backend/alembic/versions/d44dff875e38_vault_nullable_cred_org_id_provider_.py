"""vault: nullable cred org_id, provider credential_id fk

Revision ID: d44dff875e38
Revises: bd12d3efd95b
Create Date: 2026-05-06 17:29:01.148772

"""
import os
from datetime import UTC, datetime
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "d44dff875e38"
down_revision: Union[str, Sequence[str], None] = "bd12d3efd95b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _migrate_existing_api_keys() -> None:
    """Encrypt every Provider.api_key into a Credential row, set credential_id.

    Reads CUBEPLEX_AUTH__VAULT_KEY from env (or auth.vault_key from config) and
    uses MultiFernet so the primary key in the rotation list does the encryption,
    matching the runtime FernetBackend.
    """
    from cubeplex.config import config
    from cubeplex.credentials.encryption import FernetBackend
    from cubeplex.credentials.keys import parse_vault_keys
    from cubeplex.models.public_id import generate_public_id

    raw_key = os.getenv("CUBEPLEX_AUTH__VAULT_KEY") or config.get("auth.vault_key")
    bind = op.get_bind()
    rows = list(
        bind.execute(
            sa.text(
                "SELECT id, org_id, name, api_key, created_by_user_id "
                "FROM providers WHERE api_key IS NOT NULL AND api_key <> ''"
            )
        )
    )
    if not rows:
        return
    if not raw_key:
        raise RuntimeError(
            "CUBEPLEX_AUTH__VAULT_KEY must be set to migrate existing Provider.api_key "
            "values into the credential vault."
        )
    backend = FernetBackend(parse_vault_keys(str(raw_key)))
    fernet = backend._fernet  # noqa: SLF001 -- intentional, sync use within migration
    now = datetime.now(UTC)

    for row in rows:
        cred_id = generate_public_id("cred")
        ciphertext = fernet.encrypt(row.api_key.encode("utf-8"))
        bind.execute(
            sa.text(
                "INSERT INTO credentials (id, org_id, kind, name, value_encrypted,"
                " cred_metadata, created_by_user_id, created_at, updated_at)"
                " VALUES (:id, :org_id, :kind, :name, :value, :metadata::jsonb,"
                "         :user_id, :now, :now)"
            ),
            {
                "id": cred_id,
                "org_id": row.org_id,
                "kind": "provider_api_key",
                "name": row.name,
                "value": ciphertext,
                "metadata": "{}",
                "user_id": row.created_by_user_id,
                "now": now,
            },
        )
        bind.execute(
            sa.text("UPDATE providers SET credential_id = :cred WHERE id = :pid"),
            {"cred": cred_id, "pid": row.id},
        )


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        "credentials",
        "org_id",
        existing_type=sa.VARCHAR(length=20),
        nullable=True,
    )
    op.alter_column(
        "credentials",
        "created_by_user_id",
        existing_type=sa.VARCHAR(length=20),
        nullable=True,
    )
    op.drop_constraint(op.f("uq_credential_org_kind_name"), "credentials", type_="unique")
    op.create_index(
        "uq_credential_org_kind_name",
        "credentials",
        ["org_id", "kind", "name"],
        unique=True,
        postgresql_where="org_id IS NOT NULL",
    )
    op.create_index(
        "uq_credential_system_kind_name",
        "credentials",
        ["kind", "name"],
        unique=True,
        postgresql_where="org_id IS NULL",
    )
    op.add_column(
        "providers",
        sa.Column(
            "credential_id", sqlmodel.sql.sqltypes.AutoString(length=20), nullable=True
        ),
    )
    op.create_index(
        op.f("ix_providers_credential_id"), "providers", ["credential_id"], unique=False
    )
    op.create_foreign_key(
        "fk_providers_credential_id_credentials",
        "providers",
        "credentials",
        ["credential_id"],
        ["id"],
    )

    _migrate_existing_api_keys()

    op.drop_column("providers", "api_key")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        "providers",
        sa.Column("api_key", sa.VARCHAR(length=512), autoincrement=False, nullable=True),
    )
    op.drop_constraint(
        "fk_providers_credential_id_credentials", "providers", type_="foreignkey"
    )
    op.drop_index(op.f("ix_providers_credential_id"), table_name="providers")
    op.drop_column("providers", "credential_id")
    op.drop_index(
        "uq_credential_system_kind_name",
        table_name="credentials",
        postgresql_where="org_id IS NULL",
    )
    op.drop_index(
        "uq_credential_org_kind_name",
        table_name="credentials",
        postgresql_where="org_id IS NOT NULL",
    )
    op.create_unique_constraint(
        op.f("uq_credential_org_kind_name"),
        "credentials",
        ["org_id", "kind", "name"],
        postgresql_nulls_not_distinct=False,
    )
    op.alter_column(
        "credentials",
        "created_by_user_id",
        existing_type=sa.VARCHAR(length=20),
        nullable=False,
    )
    op.alter_column(
        "credentials",
        "org_id",
        existing_type=sa.VARCHAR(length=20),
        nullable=False,
    )
