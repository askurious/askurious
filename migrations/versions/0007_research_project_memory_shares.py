"""AInstein: Zusammenfassung, Teilen und Kommentare

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-05 16:00:00

research_project.memory (notes on the earlier conversation for the model), .shares (people and groups who may open
the project) and .comments (their comments). Columns already there (built by create_all from the models of this
version) are left as they are.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None

COLUMNS = ('memory', 'shares', 'comments')


def upgrade():
    have = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('research_project')}
    missing = [name for name in COLUMNS if name not in have]
    if not missing:
        return
    with op.batch_alter_table('research_project', schema=None) as batch_op:
        for name in missing:
            batch_op.add_column(sa.Column(name, sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table('research_project', schema=None) as batch_op:
        for name in reversed(COLUMNS):
            batch_op.drop_column(name)
