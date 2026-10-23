"""pairing_code.agent_id: the buddy a new phone starts on (app-api 0.8.0, S-22.27)

Revision ID: 0015
Revises: 0014

`POST /pairing-codes` with `agent_id`, and `marcel-hub pair --buddy`, store the buddy on the code;
`POST /pair` gives the new phone that buddy while it is live, else the first live one. Codes made
before have none, so their phones start on the first live buddy, as before.
"""

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'pairing_code', sa.Column('agent_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True)
    )


def downgrade() -> None:
    with op.batch_alter_table('pairing_code') as batch_op:
        batch_op.drop_column('agent_id')
