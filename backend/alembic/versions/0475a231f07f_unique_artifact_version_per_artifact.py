"""unique artifact version per artifact

Revision ID: 0475a231f07f
Revises: 076f490b6099
Create Date: 2026-07-23 05:27:14.848765

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0475a231f07f"
down_revision: Union[str, Sequence[str], None] = "076f490b6099"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_unique_constraint(
        "uq_artifact_versions_artifact_version",
        "artifact_versions",
        ["artifact_id", "version"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        "uq_artifact_versions_artifact_version",
        "artifact_versions",
        type_="unique",
    )
