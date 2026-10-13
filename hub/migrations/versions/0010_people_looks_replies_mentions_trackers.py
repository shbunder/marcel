"""app-api 0.7: people, agent looks, quoted replies, mentions and trackers, in one revision

Revision ID: 0010
Revises: 0009

Every column contract 0.7 needs, so the stories that use them (S-17.x, S-18.x, S-19.2) never race
for the next revision number (S-04.15). No rows are made here: the owner's `user` row is made at
start-up (S-17.1).

- `user`: `{id, name, role, created_at}` (B-37).
- `agent`: `look_animal`, `look_color`, `look_personality` (the contract's `look`, B-44),
  `onboarded_at`, `archived_at`, and `slug`, fixed at creation and unique. The owner's agent from
  before 0.7 gets `marcel`.
- `message`: `reply_to_message_id` (B-41) and `mentions_json` (the contract's `mentions`, B-42;
  `[]` for every existing message).
- `project`: `tracker_url` (B-46).

New columns are added in place (no table rebuild), like 0005 did for `project_id`.
"""

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None

AGENT_COLUMNS = (
    'look_animal',
    'look_color',
    'look_personality',
    'onboarded_at',
    'archived_at',
    'slug',
)


def upgrade() -> None:
    op.create_table(
        'user',
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('role', sa.Enum('owner', 'member', name='userrole'), nullable=False),
        sa.Column('created_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    for name in ('look_animal', 'look_color', 'look_personality', 'slug'):
        op.add_column('agent', sa.Column(name, sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    for name in ('onboarded_at', 'archived_at'):
        op.add_column('agent', sa.Column(name, sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True))
    # The owner's agent from before 0.7 (there is one per hub): its folders are named `marcel`.
    op.execute(
        "UPDATE agent SET slug = 'marcel' WHERE id = (SELECT id FROM agent"
        " WHERE owner_user_id = 'usr_owner' ORDER BY id LIMIT 1)"
    )
    op.create_index('ix_agent_slug', 'agent', ['slug'], unique=True)
    # Plain ADD COLUMN with the foreign key inline: no table rebuild (Alembic would rebuild).
    op.execute('ALTER TABLE message ADD COLUMN reply_to_message_id VARCHAR REFERENCES message (id)')
    op.add_column(
        'message', sa.Column('mentions_json', sa.JSON(), server_default='[]', nullable=False)
    )
    op.add_column(
        'project', sa.Column('tracker_url', sqlmodel.sql.sqltypes.AutoString(), nullable=True)
    )


def downgrade() -> None:
    # Rebuilding `message` drops it while its own rows (and others) point at it; with foreign keys
    # on, SQLite refuses that. Off for the rebuild, as in 0005; `db.downgrade` drops the
    # connection afterwards so the next one has them on again.
    op.execute('PRAGMA foreign_keys=OFF')
    with op.batch_alter_table('project') as batch_op:
        batch_op.drop_column('tracker_url')
    with op.batch_alter_table('message') as batch_op:
        batch_op.drop_column('mentions_json')
        batch_op.drop_column('reply_to_message_id')
    op.drop_index('ix_agent_slug', table_name='agent')
    with op.batch_alter_table('agent') as batch_op:
        for name in reversed(AGENT_COLUMNS):
            batch_op.drop_column(name)
    op.drop_table('user')
