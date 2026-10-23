"""Organisation: wer Personen von außen hereinholt (invite_scope, invite_scope_own)

Revision ID: 0097
Revises: 0096
Create Date: 2026-10-23 18:00:00

Two columns that may stay empty (app/orgs.py SCOPES): empty means "whoever may create surveys", as before. It only
adds, so the deploy rolls; the old code never reads them.
"""
from alembic import op
import sqlalchemy as sa


revision = '0097'
down_revision = '0096'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other (deploy/docker/restart.sh, CLAUDE.md).
ROLLING = True


def upgrade():
    columns = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('organisation')}
    for name in ('invite_scope', 'invite_scope_own'):
        if name not in columns:
            op.add_column('organisation', sa.Column(name, sa.String(length=10), nullable=True))


def downgrade():
    with op.batch_alter_table('organisation') as batch:
        batch.drop_column('invite_scope_own')
        batch.drop_column('invite_scope')
