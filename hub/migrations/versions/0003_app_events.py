"""app events: the WebSocket event log that `?since=` replays from

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'app_event',
        sa.Column('user_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('type', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('data_json', sa.JSON(), nullable=False),
        sa.Column('at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint('user_id', 'id'),
    )


def downgrade() -> None:
    op.drop_table('app_event')
