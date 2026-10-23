"""Anlegen am Konto mit drei Zuständen: user_login.may_create darf leer sein

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-06 16:00:00

The setting of a site admin whether an account may create surveys of its own had two states, and its "no" did not
outweigh a group with "darf Befragungen anlegen" (security audit 2026-10-06, S17). Now it has three (app/auth.py
may_create_surveys): NULL = left to the groups, true = may, false = an explicit "no" that outweighs the groups.

Nothing changes for anybody: false meant "a group can give it", which is NULL now; true stays true. Going down turns
NULL back into false (an explicit "no" then no longer outweighs a group).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user_login', schema=None) as batch_op:
        batch_op.alter_column('may_create', existing_type=sa.Boolean(), nullable=True)
    op.get_bind().execute(sa.text('UPDATE user_login SET may_create = NULL WHERE may_create = :no'), {'no': False})


def downgrade():
    op.get_bind().execute(sa.text('UPDATE user_login SET may_create = :no WHERE may_create IS NULL'), {'no': False})
    with op.batch_alter_table('user_login', schema=None) as batch_op:
        batch_op.alter_column('may_create', existing_type=sa.Boolean(), nullable=False)
