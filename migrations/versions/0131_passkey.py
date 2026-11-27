"""Anmeldung mit Passkey: table passkey

Revision ID: 0131
Revises: 0130
Create Date: 2026-11-27 06:00:00

A passkey (WebAuthn) is a third way to sign in besides ORCID and e-mail with password (app/passkeys.py): per passkey
the credential's id and public key, the counter, the name the person gave it, when it was made and last used and
whether it is synchronised. A new table only, which the code before never reads or writes: the deploy rolls.
"""
from alembic import op
import sqlalchemy as sa


revision = '0131'
down_revision = '0130'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other (deploy/docker/restart.sh, CLAUDE.md).
ROLLING = True


def upgrade():
    bind = op.get_bind()
    if 'passkey' in sa.inspect(bind).get_table_names():
        # made from the models already: an old schema repair (0014) may have removed its key, as 0079 and 0081 say
        known = {tuple(fk['constrained_columns']) for fk in sa.inspect(bind).get_foreign_keys('passkey')}
        if ('user_id',) not in known:
            with op.batch_alter_table('passkey') as batch_op:
                batch_op.create_foreign_key('passkey_user_id_fkey', 'user', ['user_id'], ['id'], ondelete='CASCADE')
        return
    op.create_table(
        'passkey',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('credential_id', sa.String(length=1400), nullable=False),
        sa.Column('public_key', sa.Text(), nullable=False),
        sa.Column('user_handle', sa.String(length=64), nullable=False),
        sa.Column('sign_count', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=60), nullable=False),
        sa.Column('backed_up', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], name=op.f('passkey_user_id_fkey'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('passkey_pkey')),
        sa.UniqueConstraint('credential_id', name=op.f('passkey_credential_id_key')),
    )
    with op.batch_alter_table('passkey', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_passkey_user_id'), ['user_id'], unique=False)


def downgrade():
    with op.batch_alter_table('passkey', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_passkey_user_id'))
    op.drop_table('passkey')
