"""Anmeldung freischalten in der Administration: Tabellen sign_in_orcid und site_setting

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-06 09:30:00

Who may sign in with ORCID by themselves was ALLOWED_ORCIDS in the server's configuration; now a site admin keeps
it in Administration > Forschende (Ralf, 2026-10-06; app/signin.py): a list of iDs and the switch "every ORCID
holder" (site_setting). The configuration is taken over once at the start of the server (signin.adopt_config, not
here: a database made by create_all gets no migration). Tables already there (built by create_all from the models
of this version) are left as they are.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table('sign_in_orcid'):
        op.create_table('sign_in_orcid',
        sa.Column('orcid', sa.String(length=19), nullable=False),
        sa.Column('note', sa.String(length=200), nullable=False),
        sa.Column('added_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['added_by_id'], ['user.id'], name=op.f('sign_in_orcid_added_by_id_fkey'), ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('orcid', name=op.f('sign_in_orcid_pkey'))
        )
    if not inspector.has_table('site_setting'):
        op.create_table('site_setting',
        sa.Column('key', sa.String(length=40), nullable=False),
        sa.Column('value', sa.String(length=200), nullable=False),
        sa.Column('changed_by_id', sa.Integer(), nullable=True),
        sa.Column('changed_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['changed_by_id'], ['user.id'], name=op.f('site_setting_changed_by_id_fkey'), ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('key', name=op.f('site_setting_pkey'))
        )


def downgrade():
    op.drop_table('site_setting')
    op.drop_table('sign_in_orcid')
