"""Rights per survey type (app/kind_access.py, konzept-rechte-befragungstypen-2026-10-15.md): what an account may see
or edit of questionnaires, live polls, interview guides and diary studies, what an iD on the sign-in list gets at its
first sign-in, and the preset a group gives new accounts.

Additive only: three JSON columns that may stay empty (empty = all four types); older containers ignore them.
"""
import sqlalchemy as sa
from alembic import op

revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None
ROLLING = True

COLUMNS = (("user_login", "kinds"), ("sign_in_orcid", "kinds"), ("workgroup", "kinds_preset"))


def upgrade():
    inspector = sa.inspect(op.get_bind())
    for table, column in COLUMNS:
        if column not in {c["name"] for c in inspector.get_columns(table)}:
            op.add_column(table, sa.Column(column, sa.JSON(), nullable=True))


def downgrade():
    # SQLite copies the tables for this; the revision triggers name them and go first (app/data_revision.py)
    from app.data_revision import install, uninstall

    uninstall(op.get_bind())
    for table, column in COLUMNS:
        with op.batch_alter_table(table) as batch:
            batch.drop_column(column)
    install(op.get_bind())
