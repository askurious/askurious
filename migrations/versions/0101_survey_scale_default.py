"""Standard der Skalen einer Befragung (Spalte survey.scale_default)

Revision ID: 0101
Revises: 0100
Create Date: 2026-10-27 14:30:00

One column that may stay empty: the standard of the Likert scales of a survey (points, answer scale, labels, numbers;
app/scale_default.py, user test 2026-10-27, point 10). It only adds, so the deploy rolls; the old code never reads it.
"""
from alembic import op
import sqlalchemy as sa


revision = '0101'
down_revision = '0100'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other (deploy/docker/restart.sh, CLAUDE.md).
ROLLING = True


def upgrade():
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("survey")}
    if "scale_default" not in columns:
        op.add_column("survey", sa.Column("scale_default", sa.JSON(), nullable=True))  # no table copy: survey has triggers


def downgrade():
    # SQLite copies the tables for this; the revision triggers name them and go first (app/data_revision.py)
    from app.data_revision import install, uninstall

    uninstall(op.get_bind())
    with op.batch_alter_table("survey") as batch:
        batch.drop_column("scale_default")
    install(op.get_bind())
