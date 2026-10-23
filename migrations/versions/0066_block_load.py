"""Survey setting "Einfache Blöcke am Stück laden" (experimental "blockload", app/blockload.py).

Additive only: a boolean column with a server default; older participant containers ignore it.
"""
import sqlalchemy as sa
from alembic import op

revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None
ROLLING = True


def upgrade():
    if "block_load" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("survey")}:
        op.add_column("survey", sa.Column("block_load", sa.Boolean(), nullable=False, server_default="0"))


def downgrade():
    # SQLite copies the table for this; the revision triggers name it and go first (app/data_revision.py)
    from app.data_revision import install, uninstall

    uninstall(op.get_bind())
    with op.batch_alter_table("survey") as batch:
        batch.drop_column("block_load")
    install(op.get_bind())
