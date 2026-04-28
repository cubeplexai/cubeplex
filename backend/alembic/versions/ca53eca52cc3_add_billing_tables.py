"""add billing tables

Revision ID: ca53eca52cc3
Revises: 704767f28f2b
Create Date: 2026-04-28 14:33:19.924174

"""
from typing import Sequence, Union

import sqlmodel.sql.sqltypes
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'ca53eca52cc3'
down_revision: Union[str, Sequence[str], None] = '704767f28f2b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('billing_events',
    sa.Column('org_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('workspace_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('user_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('conversation_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('event_type', sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
    sa.Column('cost_amount_micro', sa.Integer(), nullable=False),
    sa.Column('currency', sqlmodel.sql.sqltypes.AutoString(length=3), nullable=False),
    sa.Column('started_at', sa.DateTime(), nullable=False),
    sa.Column('ended_at', sa.DateTime(), nullable=False),
    sa.Column('duration_ms', sa.Integer(), nullable=False),
    sa.Column('status', sqlmodel.sql.sqltypes.AutoString(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_billing_events_conversation', 'billing_events', ['conversation_id'], unique=False)
    op.create_index(op.f('ix_billing_events_org_id'), 'billing_events', ['org_id'], unique=False)
    op.create_index('ix_billing_events_org_time', 'billing_events', ['org_id', 'started_at'], unique=False)
    op.create_index('ix_billing_events_org_ws_time', 'billing_events', ['org_id', 'workspace_id', 'started_at'], unique=False)
    op.create_index(op.f('ix_billing_events_user_id'), 'billing_events', ['user_id'], unique=False)
    op.create_index(op.f('ix_billing_events_workspace_id'), 'billing_events', ['workspace_id'], unique=False)
    op.create_table('billing_llm_events',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('billing_event_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=False),
    sa.Column('provider', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('model_id', sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False),
    sa.Column('input_tokens', sa.Integer(), nullable=False),
    sa.Column('output_tokens', sa.Integer(), nullable=False),
    sa.Column('cache_read_tokens', sa.Integer(), nullable=False),
    sa.Column('cache_write_tokens', sa.Integer(), nullable=False),
    sa.Column('price_input_per_mtok_micro', sa.Integer(), nullable=False),
    sa.Column('price_output_per_mtok_micro', sa.Integer(), nullable=False),
    sa.Column('price_cache_read_per_mtok_micro', sa.Integer(), nullable=False),
    sa.Column('price_cache_write_per_mtok_micro', sa.Integer(), nullable=False),
    sa.Column('parent_run_id', sqlmodel.sql.sqltypes.AutoString(length=36), nullable=True),
    sa.Column('subagent_depth', sa.Integer(), nullable=False),
    sa.Column('error_class', sqlmodel.sql.sqltypes.AutoString(length=128), nullable=True),
    sa.ForeignKeyConstraint(['billing_event_id'], ['billing_events.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_billing_llm_events_billing_event_id'), 'billing_llm_events', ['billing_event_id'], unique=False)
    op.create_index('ix_billing_llm_parent', 'billing_llm_events', ['parent_run_id'], unique=False)
    op.create_index('ix_billing_llm_provider_model', 'billing_llm_events', ['provider', 'model_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_billing_llm_provider_model', table_name='billing_llm_events')
    op.drop_index('ix_billing_llm_parent', table_name='billing_llm_events')
    op.drop_index(op.f('ix_billing_llm_events_billing_event_id'), table_name='billing_llm_events')
    op.drop_table('billing_llm_events')
    op.drop_index(op.f('ix_billing_events_workspace_id'), table_name='billing_events')
    op.drop_index(op.f('ix_billing_events_user_id'), table_name='billing_events')
    op.drop_index('ix_billing_events_org_ws_time', table_name='billing_events')
    op.drop_index('ix_billing_events_org_time', table_name='billing_events')
    op.drop_index(op.f('ix_billing_events_org_id'), table_name='billing_events')
    op.drop_index('ix_billing_events_conversation', table_name='billing_events')
    op.drop_table('billing_events')
