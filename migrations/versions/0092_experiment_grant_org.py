"""Experimentelle Funktionen für ganze Organisationen: Spalte experiment_grant.org_id

Revision ID: 0092
Revises: 0091
Create Date: 2026-10-20 11:00:00

A site admin switches an experimental feature on for a whole organisation (app/experimental.py, Ralf 2026-10-20),
live for everyone in it as app/orgs.py counts them (own row, a group of it, home "main"). A column that may stay
empty, with its key and index: it only adds, so the deploy rolls. The old code would read a row of an organisation as
one for everyone (it asks only for empty user_id and group_id), but such rows come only from the new admin page, after
the deploy.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0092'
down_revision = '0091'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other, and the one not yet restarted runs the
# old code against the new schema for a moment (deploy/docker/restart.sh, CLAUDE.md). Dropping or renaming: False.
ROLLING = True


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'org_id' not in {c['name'] for c in inspector.get_columns('experiment_grant')}:
        op.add_column('experiment_grant', sa.Column('org_id', sa.Integer(), nullable=True))
    if ('org_id',) not in {tuple(fk['constrained_columns']) for fk in sa.inspect(bind).get_foreign_keys('experiment_grant')}:
        if bind.dialect.name == 'sqlite':  # SQLite copies the table for a key; the revision triggers go first (0071)
            from app.data_revision import install, uninstall

            uninstall(bind)
            with op.batch_alter_table('experiment_grant') as batch:
                batch.create_foreign_key('experiment_grant_org_id_fkey', 'organisation', ['org_id'], ['id'],
                                         ondelete='CASCADE')
            install(bind)
        else:
            op.create_foreign_key('experiment_grant_org_id_fkey', 'experiment_grant', 'organisation', ['org_id'],
                                  ['id'], ondelete='CASCADE')
    if 'ix_experiment_grant_org_id' not in {i['name'] for i in sa.inspect(bind).get_indexes('experiment_grant')}:
        op.create_index('ix_experiment_grant_org_id', 'experiment_grant', ['org_id'])


def downgrade():
    from app.data_revision import install, uninstall

    uninstall(op.get_bind())
    op.drop_index('ix_experiment_grant_org_id', table_name='experiment_grant')
    with op.batch_alter_table('experiment_grant') as batch:
        batch.drop_column('org_id')
    install(op.get_bind())
