"""pinned items: `artifact.pinned`, set when the owner moves an item to another project

Revision ID: 0007
Revises: 0006

An item follows its task when the task moves, until the owner pins it somewhere on its own
(`POST /api/artifacts/{id}/move`). Every existing item starts unpinned: none was ever moved on its
own before this.
"""

from alembic import op

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Plain ADD COLUMN: no table rebuild while other rows point at `artifact` (see 0006).
    op.execute('ALTER TABLE artifact ADD COLUMN pinned BOOLEAN NOT NULL DEFAULT 0')


def downgrade() -> None:
    # The rebuild drops `artifact` while other rows point at it: foreign keys off (see 0006).
    op.execute('PRAGMA foreign_keys=OFF')
    with op.batch_alter_table('artifact') as batch_op:
        batch_op.drop_column('pinned')
