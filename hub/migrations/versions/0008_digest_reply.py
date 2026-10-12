"""digest replies: `artifact_version.in_reply_to`, the `digest_id` a version answers

Revision ID: 0008
Revises: 0007

The brain publishes a digest with `in_reply_to` set to the `digest_due` event's `digest_id`
(brain-api 0.1.1); the hub keeps it on the version, so it knows the digest by it rather than by
its title. Every existing version answers nothing.
"""

from alembic import op

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Plain ADD COLUMN: no table rebuild (see 0006).
    op.execute('ALTER TABLE artifact_version ADD COLUMN in_reply_to VARCHAR')


def downgrade() -> None:
    with op.batch_alter_table('artifact_version') as batch_op:
        batch_op.drop_column('in_reply_to')
