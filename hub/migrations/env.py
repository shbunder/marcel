from alembic import context
from sqlmodel import SQLModel

from marcel_hub import models  # noqa: F401  (registers the tables on the metadata)

engine = context.config.attributes['engine']

with engine.connect() as connection:
    context.configure(
        connection=connection,
        target_metadata=SQLModel.metadata,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()
