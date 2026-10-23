"""Darstellung am Konto

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-05 09:00:00

user.display: the look of the researchers' pages for this person (app/display.py, concept
darstellung-forschende-konzept.md): light or dark, colour style, text size. Empty for every account that exists;
empty means Askurious as it comes. A column that is there already is left as it is.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def _has(table, column):
    return column in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    if not _has('user', 'display'):
        with op.batch_alter_table('user', schema=None) as batch_op:
            batch_op.add_column(sa.Column('display', sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('display')
