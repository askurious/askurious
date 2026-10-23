"""Domain, unter der ein Konto oder eine Befragung angelegt wurde: user.created_host, survey.created_host

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-09 22:00:00

A later split into one database per brand (askurious.de, polltergeist.de; datenbank-je-marke-konzept.md, Ralf
2026-10-09) needs to know what belongs to which; the database could not tell so far. New rows get the domain of the
request they are made in (models._created_host); rows from before stay empty. Columns that may stay empty: they only
add, so the deploy rolls.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0029'
down_revision = '0028'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other, and the one not yet restarted runs the
# old code against the new schema for a moment (deploy/docker/restart.sh, CLAUDE.md). Dropping or renaming: False.
ROLLING = True


def upgrade():
    for table in ('user', 'survey'):
        have = {c['name'] for c in sa.inspect(op.get_bind()).get_columns(table)}
        if 'created_host' not in have:
            with op.batch_alter_table(table, schema=None) as batch_op:
                batch_op.add_column(sa.Column('created_host', sa.String(length=253), nullable=True))


def downgrade():
    for table in ('survey', 'user'):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_column('created_host')
