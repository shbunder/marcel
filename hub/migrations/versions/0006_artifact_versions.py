"""artifact versions and project boards: artifact_version, the current version on artifact,
artifact_id on message, and a board artifact for every project

Revision ID: 0006
Revises: 0005

Every existing artifact gets its version 1. Every existing project gets its board (kind `board`,
under the `board_artifact_id` it was given in 0005); the hub writes the board's content when it
starts (`boards.BoardKeeper.start`).
"""

from datetime import UTC, datetime

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None

BOARD_AUTHOR = 'the hub'
EPOCH = '1970-01-01 00:00:00.000000'


def upgrade() -> None:
    version = op.create_table(
        'artifact_version',
        sa.Column('artifact_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('url', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('content_type', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('size_bytes', sa.Integer(), nullable=True),
        sa.Column('summary', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('author', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('task_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('created_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(['artifact_id'], ['artifact.id']),
        sa.PrimaryKeyConstraint('artifact_id', 'version'),
    )
    assert version is not None

    # Plain ADD COLUMN: no table rebuild while other rows point at `artifact` and `message`.
    op.execute('ALTER TABLE artifact ADD COLUMN current_version INTEGER NOT NULL DEFAULT 1')
    op.execute('ALTER TABLE artifact ADD COLUMN updated_by VARCHAR')
    op.execute(f"ALTER TABLE artifact ADD COLUMN updated_at DATETIME NOT NULL DEFAULT '{EPOCH}'")
    op.execute('ALTER TABLE message ADD COLUMN artifact_id VARCHAR')
    op.execute(
        'UPDATE artifact SET updated_at = created_at, updated_by = '
        '(SELECT t.title FROM task t WHERE t.id = artifact.task_id)'
    )
    op.execute(
        'INSERT INTO artifact_version (artifact_id, version, url, content_type, size_bytes, '
        'author, task_id, created_at) SELECT id, 1, url, content_type, size_bytes, updated_by, '
        'task_id, created_at FROM artifact'
    )

    # A board for every project (B-35), empty until the hub writes it.
    conn = op.get_bind()
    now = datetime.now(UTC)
    artifact = sa.table(
        'artifact',
        *(
            sa.column(c)
            for c in (
                'id',
                'project_id',
                'kind',
                'title',
                'content_type',
                'trusted',
                'self_change',
                'current_version',
                'updated_by',
            )
        ),  # fmt: skip
        sa.column('meta_json', sa.JSON()),
        sa.column('created_at', sqlmodel.sql.sqltypes.UTCDateTime()),
        sa.column('updated_at', sqlmodel.sql.sqltypes.UTCDateTime()),
    )
    projects = conn.execute(sa.text('SELECT id, name, board_artifact_id FROM project')).all()
    for project_id, name, board_id in projects:
        conn.execute(
            artifact.insert().values(
                id=board_id, project_id=project_id, kind='board', title=f'{name} — board',
                content_type='application/json', trusted=False, self_change=False, meta_json={},
                current_version=1, updated_by=BOARD_AUTHOR, created_at=now, updated_at=now,
            )
        )  # fmt: skip
        conn.execute(
            version.insert().values(
                artifact_id=board_id, version=1, content_type='application/json',
                author=BOARD_AUTHOR, created_at=now,
            )
        )  # fmt: skip


def downgrade() -> None:
    # Rebuilding `artifact` and `message` drops each while other rows point at it; with foreign
    # keys on, SQLite refuses that. Off for the rebuild (see 0005); `db.downgrade` drops the
    # connection afterwards, so the next one has them on again.
    op.execute('PRAGMA foreign_keys=OFF')
    op.drop_table('artifact_version')
    # Before 0006 there were no boards and no `artifact` messages: they go.
    op.execute("DELETE FROM message WHERE kind = 'artifact'")
    op.execute("DELETE FROM artifact WHERE kind = 'board'")
    with op.batch_alter_table('message') as batch_op:
        batch_op.drop_column('artifact_id')
    with op.batch_alter_table('artifact') as batch_op:
        batch_op.drop_column('updated_at')
        batch_op.drop_column('updated_by')
        batch_op.drop_column('current_version')
