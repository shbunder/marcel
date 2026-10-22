"""One person, many buddies: the people model goes (app-api 0.8.0, ADR-013, S-22.11)

Revision ID: 0014
Revises: 0013

Every person is folded into the owner's row (`usr_owner`). Their agents become the owner's
buddies and keep their slug, so their memory folder and brain folder stay where they are. Each
phone stays paired and gets `device.agent_id`: the first live agent of the person it belonged to,
so a family member's phone keeps showing their own buddy. `user.role` goes.

A family member's unused pairing codes are dropped (they would now pair a phone without saying
which buddy it shows; they last ten minutes anyway), and so are their saved WebSocket events (the
event ids count per person and would clash with the owner's; a phone that reconnects with its old
`?since=` gets the owner's events after it, or `resync.required` when the owner's log is shorter).

The downgrade gives the columns back (every row `owner`) but cannot tell the people apart again.
"""

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None

OWNER = 'usr_owner'


def upgrade() -> None:
    op.add_column(
        'device', sa.Column('agent_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True)
    )
    bind = op.get_bind()
    # Each phone shows the first live agent of its old person (with its main conversation, the
    # oldest id first, as `/me` lists them), else the owner's; with none at all it stays empty.
    first_live = (
        'SELECT a.id FROM agent a WHERE a.owner_user_id = {who} AND a.archived_at IS NULL '
        'AND a.main_conversation_id IS NOT NULL ORDER BY a.id LIMIT 1'
    )
    bind.execute(
        sa.text(
            'UPDATE device SET agent_id = COALESCE(('
            + first_live.format(who='device.user_id')
            + '), ('
            + first_live.format(who=f"'{OWNER}'")
            + '))'
        )
    )
    bind.execute(sa.text('DELETE FROM pairing_code WHERE user_id != :o'), {'o': OWNER})
    bind.execute(sa.text('DELETE FROM app_event WHERE user_id != :o'), {'o': OWNER})
    bind.execute(sa.text('UPDATE agent SET owner_user_id = :o'), {'o': OWNER})
    bind.execute(sa.text('UPDATE device SET user_id = :o'), {'o': OWNER})
    bind.execute(sa.text('DELETE FROM "user" WHERE id != :o'), {'o': OWNER})
    with op.batch_alter_table('user') as batch_op:
        batch_op.drop_column('role')


def downgrade() -> None:
    with op.batch_alter_table('user') as batch_op:
        batch_op.add_column(
            sa.Column(
                'role',
                sa.Enum('owner', 'member', name='userrole'),
                server_default='owner',
                nullable=False,
            )
        )
    with op.batch_alter_table('device') as batch_op:
        batch_op.drop_column('agent_id')
