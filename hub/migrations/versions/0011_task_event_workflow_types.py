"""task_event.type takes a workflow's four event types (S-04.16, app-api 0.7.3)

Revision ID: 0011
Revises: 0010

`workflow_start`, `workflow_stop`, `workflow_agent_start`, `workflow_agent_stop`. On SQLite the
enum is a VARCHAR sized to its longest value with no check constraint, so the stored rows need no
change; only the declared length grows (14 → 20), which keeps the schema equal to the models.
"""

import sqlalchemy as sa
from alembic import op

revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None

OLD = ('text', 'tool_call', 'tool_result', 'diff', 'permission', 'subagent_start', 'subagent_stop',
       'error', 'raw', 'state', 'progress', 'artifact', 'note')  # fmt: skip
NEW = (*OLD[:7], 'workflow_start', 'workflow_stop', 'workflow_agent_start', 'workflow_agent_stop',
       *OLD[7:])  # fmt: skip


def upgrade() -> None:
    with op.batch_alter_table('task_event') as batch_op:
        batch_op.alter_column(
            'type',
            existing_type=sa.Enum(*OLD, name='taskeventtype'),
            type_=sa.Enum(*NEW, name='taskeventtype'),
            existing_nullable=False,
        )


def downgrade() -> None:
    # Workflow events cannot be kept under the old types: they go.
    op.execute(
        'DELETE FROM task_event WHERE type IN '
        "('workflow_start', 'workflow_stop', 'workflow_agent_start', 'workflow_agent_stop')"
    )
    with op.batch_alter_table('task_event') as batch_op:
        batch_op.alter_column(
            'type',
            existing_type=sa.Enum(*NEW, name='taskeventtype'),
            type_=sa.Enum(*OLD, name='taskeventtype'),
            existing_nullable=False,
        )
