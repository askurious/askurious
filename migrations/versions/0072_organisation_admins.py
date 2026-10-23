"""Organisations, stages 3 and 4 (app/orgs.py, konzept-abteilungen-2026-10-15.md): the full name for survey texts, the
user types an organisation's
admins may give, the themes of an institution given to it and its default designs, designs shared with a whole
organisation, example surveys of an organisation.

Additive only: a column that may stay empty and two with a server default; older containers ignore them.
"""
import sqlalchemy as sa
from alembic import op

revision = "0072"
down_revision = "0071"
branch_labels = None
depends_on = None
ROLLING = True

COLUMNS = (
    ("organisation", sa.Column("allowed_types", sa.JSON(), nullable=True)),
    ("organisation", sa.Column("long_name", sa.String(200), nullable=False, server_default="")),
    ("organisation", sa.Column("themes", sa.JSON(), nullable=True)),
    ("organisation", sa.Column("theme_light", sa.String(40), nullable=True)),
    ("organisation", sa.Column("theme_dark", sa.String(40), nullable=True)),
    ("design", sa.Column("org_shared", sa.Boolean(), nullable=False, server_default="0")),
    ("survey", sa.Column("org_showcase", sa.Boolean(), nullable=False, server_default="0")),
)


def upgrade():
    bind = op.get_bind()
    for table, column in COLUMNS:
        if column.name not in {c["name"] for c in sa.inspect(bind).get_columns(table)}:
            op.add_column(table, column)


def downgrade():
    # SQLite copies the tables for this; the revision triggers name them and go first (app/data_revision.py)
    from app.data_revision import install, uninstall

    uninstall(op.get_bind())
    for table, column in COLUMNS:
        with op.batch_alter_table(table) as batch:
            batch.drop_column(column.name)
    install(op.get_bind())
