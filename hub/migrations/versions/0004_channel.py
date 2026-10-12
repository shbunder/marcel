"""channel gateway: queued hub → session frames and stored call ids

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'channel_frame',
        sa.Column('target', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('seq', sa.Integer(), nullable=False),
        sa.Column('frame_json', sa.JSON(), nullable=False),
        sa.Column('acked', sa.Boolean(), nullable=False),
        sa.Column('created_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint('target', 'seq'),
    )
    op.create_table(
        'channel_call',
        sa.Column('call_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('target', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint('call_id'),
    )


def downgrade() -> None:
    op.drop_table('channel_call')
    op.drop_table('channel_frame')
