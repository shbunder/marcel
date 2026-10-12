"""projects: the project table, an inbox per agent, and project_id on tasks, messages and artifacts

Revision ID: 0004
Revises: 0003

Every existing agent gets its `inbox`, and every existing task and artifact is filed there (an
artifact under its task's project). `task.project_id` is added as a plain column with a foreign key
(SQLite cannot add a NOT NULL column to a table with rows, and rebuilding `task` would break the
rows that point at it), so two triggers refuse a task without a project instead.
"""

import secrets
import time
from datetime import UTC, datetime

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def _id(prefix: str) -> str:
    return f'{prefix}_{int(time.time() * 1000):011x}{secrets.token_hex(4)}'


def upgrade() -> None:
    project = op.create_table(
        'project',
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('agent_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('slug', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('goal', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('repo', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('folder', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('instructions', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('claude_project_url', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('board_artifact_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('archived', sa.Boolean(), nullable=False),
        sa.Column('created_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column('updated_at', sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(['agent_id'], ['agent.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    assert project is not None
    op.create_index('ix_project_agent_id', 'project', ['agent_id'], unique=False)
    op.create_index(
        'ix_project_live_slug',
        'project',
        ['agent_id', 'slug'],
        unique=True,
        sqlite_where=sa.text('archived = 0'),
    )

    # Plain ADD COLUMN with the foreign key inline: no table rebuild (Alembic would rebuild).
    for table in ('task', 'message', 'artifact'):
        op.execute(f'ALTER TABLE {table} ADD COLUMN project_id VARCHAR REFERENCES project (id)')
    op.create_index('ix_task_project_id', 'task', ['project_id'], unique=False)
    op.create_index('ix_artifact_project_id', 'artifact', ['project_id'], unique=False)

    # An inbox for every agent, and every existing task and artifact filed in it.
    conn = op.get_bind()
    now = datetime.now(UTC)
    for (agent_id,) in conn.execute(sa.text('SELECT id FROM agent')).all():
        conn.execute(
            project.insert().values(
                id=_id('prj'),
                agent_id=agent_id,
                slug='inbox',
                name='Inbox',
                board_artifact_id=_id('art'),
                archived=False,
                created_at=now,
                updated_at=now,
            )
        )
    op.execute(
        'UPDATE task SET project_id = (SELECT p.id FROM project p WHERE p.agent_id = task.agent_id'
        " AND p.slug = 'inbox') WHERE project_id IS NULL"
    )
    op.execute(
        'UPDATE artifact SET project_id = (SELECT t.project_id FROM task t'
        ' WHERE t.id = artifact.task_id) WHERE project_id IS NULL AND task_id IS NOT NULL'
    )
    op.execute(
        "UPDATE artifact SET project_id = (SELECT p.id FROM project p WHERE p.slug = 'inbox'"
        ' ORDER BY p.agent_id LIMIT 1) WHERE project_id IS NULL'
    )

    # Every task belongs to exactly one project (B-30).
    for when in ('INSERT', 'UPDATE OF project_id'):
        name = 'task_needs_project_' + when.split()[0].lower()
        op.execute(
            f'CREATE TRIGGER {name} BEFORE {when} ON task WHEN NEW.project_id IS NULL '
            "BEGIN SELECT RAISE(ABORT, 'A task must belong to a project.'); END"
        )


def downgrade() -> None:
    # Rebuilding `artifact`, `message` and `task` drops each table while other rows point at it;
    # with foreign keys on, SQLite refuses that. Off for the rebuild: this runs before the
    # migration's transaction begins, so it takes effect. Inside the transaction, turning them
    # back on would be ignored, so `db.downgrade` drops the connection afterwards instead.
    op.execute('PRAGMA foreign_keys=OFF')
    op.execute('DROP TRIGGER IF EXISTS task_needs_project_insert')
    op.execute('DROP TRIGGER IF EXISTS task_needs_project_update')
    for table, indexed in (('artifact', True), ('message', False), ('task', True)):
        with op.batch_alter_table(table) as batch_op:
            if indexed:
                batch_op.drop_index(f'ix_{table}_project_id')
            batch_op.drop_column('project_id')
    op.drop_index('ix_project_live_slug', table_name='project')
    op.drop_index('ix_project_agent_id', table_name='project')
    op.drop_table('project')
