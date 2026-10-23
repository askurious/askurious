"""AInstein: Projekte unter einem zufälligen Code statt der laufenden Nummer

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-05 12:00:00

research_project.code: 8 random hex digits, the address of a project (/ainstein/<code>) instead of its id, so projects
cannot be counted through. Existing projects get a code of their own. A table that has the column already (built by
create_all from the models of this version) is left as it is.
"""
import secrets

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade():
    if 'code' in {c['name'] for c in sa.inspect(op.get_bind()).get_columns('research_project')}:
        return
    with op.batch_alter_table('research_project', schema=None) as batch_op:
        batch_op.add_column(sa.Column('code', sa.String(length=8), nullable=True))
    connection = op.get_bind()
    table = sa.table('research_project', sa.column('id', sa.Integer), sa.column('code', sa.String))
    taken = set()
    for (pid,) in connection.execute(sa.select(table.c.id)).fetchall():
        code = secrets.token_hex(4)
        while code in taken:
            code = secrets.token_hex(4)
        taken.add(code)
        connection.execute(table.update().where(table.c.id == pid).values(code=code))
    with op.batch_alter_table('research_project', schema=None) as batch_op:
        batch_op.alter_column('code', existing_type=sa.String(length=8), nullable=False)
        batch_op.create_unique_constraint(batch_op.f('research_project_code_key'), ['code'])


def downgrade():
    with op.batch_alter_table('research_project', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('research_project_code_key'), type_='unique')
        batch_op.drop_column('code')
