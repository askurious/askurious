"""Alembic environment of Askurious: the models of app/models.py are the target. Used at start-up (app/migrations.py
hands over a connection) and by "flask db ..." during development (Flask-Migrate, the app's engine).

render_as_batch: SQLite changes a table by copying it (batch mode); PostgreSQL alters it in place. The objects that
live outside the models are left out of the comparison (app/migrations.py, include_object)."""
from alembic import context

import app.models  # noqa: F401  (the models fill db.metadata)
from app.extensions import db
from app.migrations import include_object

config = context.config


def _run(connection):
    context.configure(connection=connection, target_metadata=db.metadata, render_as_batch=True, compare_type=True,
                      include_object=include_object, transaction_per_migration=True)
    with context.begin_transaction():
        context.run_migrations()


connection = config.attributes.get("connection")
if connection is not None:
    _run(connection)
else:
    with db.engine.connect() as connection:
        _run(connection)
