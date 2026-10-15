"""task.kickoff: the project's kickoff quest (brain-api 0.1.5, S-20.3)

Revision ID: 0013
Revises: 0012

Only a kickoff quest may set or replace its project's tracker. Every existing task is not one.
"""

import sqlalchemy as sa
from alembic import op

revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('task', sa.Column('kickoff', sa.Boolean(), server_default='0', nullable=False))


def downgrade() -> None:
    # Rebuilding `task` while other rows point at it needs foreign keys off, as in 0010.
    op.execute('PRAGMA foreign_keys=OFF')
    with op.batch_alter_table('task') as batch_op:
        batch_op.drop_column('kickoff')
