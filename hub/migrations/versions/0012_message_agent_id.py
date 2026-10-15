"""message.agent_id: the agent that wrote an `agent` message (app-api 0.7.2, S-02.10 follow-up)

Revision ID: 0012
Revises: 0011

Every `agent` message gets its conversation's agent, the existing ones too; `user` and `system`
messages keep none.
"""

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'message', sa.Column('agent_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True)
    )
    op.execute(
        'UPDATE message SET agent_id = (SELECT agent_id FROM conversation '
        "WHERE conversation.id = message.conversation_id) WHERE role = 'agent'"
    )


def downgrade() -> None:
    # Rebuilding `message` while other rows point at it needs foreign keys off, as in 0010.
    op.execute('PRAGMA foreign_keys=OFF')
    with op.batch_alter_table('message') as batch_op:
        batch_op.drop_column('agent_id')
