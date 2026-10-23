"""device relay target: pushes through the push relay (app-api 0.8.0 `kind: relay`, S-22.15)

Revision ID: 0016
Revises: 0015

`PUT /devices/{id}/push` with `kind: relay` stores the relay's address, the phone's handle there,
the handle's send secret and the phone's X25519 public key. Phones paired before have none, and
keep the push target they had.
"""

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None

COLUMNS = ('relay_url', 'relay_handle', 'relay_secret', 'relay_device_key')


def upgrade() -> None:
    for name in COLUMNS:
        op.add_column('device', sa.Column(name, sqlmodel.sql.sqltypes.AutoString(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('device') as batch_op:
        for name in reversed(COLUMNS):
            batch_op.drop_column(name)
