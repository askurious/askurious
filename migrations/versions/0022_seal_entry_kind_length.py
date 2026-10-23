"""Siegelkette: seal_entry.kind fasst „fieldstarted“ und „fieldclosed“

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-08 20:00:00

The kinds "fieldstarted" and "fieldclosed" (start and close of the fieldwork, app/seal.py field_event) are 12 and 11
characters; the column held 10. SQLite does not check the length, PostgreSQL refused the entry, so starting or closing
the fieldwork of a survey with the seal chain failed there (audit 2026-10-08). SQLite gets the same type, so the
schema matches the models (batch: the table is copied once).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0022'
down_revision = '0021'
branch_labels = None
depends_on = None


def _kind(new, old):
    with op.batch_alter_table('seal_entry', schema=None) as batch_op:
        batch_op.alter_column('kind', type_=sa.String(length=new), existing_type=sa.String(length=old),
                              existing_nullable=False)


def upgrade():
    _kind(20, 10)


def downgrade():
    _kind(10, 20)
