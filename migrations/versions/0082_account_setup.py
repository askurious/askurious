"""The setup assistant after the first sign-in (app/account_setup.py, konto-einrichtung-konzept.md, Ralf 2026-10-18):
user.setup_due, true for accounts made from now on until the person finishes or ends the assistant.

Rolling: only a column with a server default is added; accounts from before keep false and never see the assistant,
and the code before neither reads nor writes it.
"""
import sqlalchemy as sa
from alembic import op

revision = "0082"
down_revision = "0081"
branch_labels = None
depends_on = None
ROLLING = True


def upgrade():
    if "setup_due" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("user")}:
        op.add_column("user", sa.Column("setup_due", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    with op.batch_alter_table("user") as batch:
        batch.drop_column("setup_due")
