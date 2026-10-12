"""brain rollovers: `brain_rollover`, one row per rollover, kept apart from the app event log

Revision ID: 0009
Revises: 0008

The notebook lists Marcel's past rollovers (app-api 0.6 `GET /agents/{agent_id}/brain/rollovers`).
The app event log keeps only the latest 2000 events, so each rollover is also stored here, written
with its `brain.rollover` event. Rollovers from before this revision are not recovered.
"""

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'brain_rollover',
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('agent_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column('reason', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('kept', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('old_session_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('new_session_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.ForeignKeyConstraint(['agent_id'], ['agent.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('brain_rollover', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_brain_rollover_agent_id'), ['agent_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_brain_rollover_at'), ['at'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('brain_rollover', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_brain_rollover_at'))
        batch_op.drop_index(batch_op.f('ix_brain_rollover_agent_id'))
    op.drop_table('brain_rollover')
