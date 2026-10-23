"""Seal chain: one timestamp of the DFN per round for all chains (app/seal.py, anchor_round, SEAL_MERKLE;
konzept-merkle-zeitstempel-2026-10-16.md). A timestamp row keeps the path from its chain's hash to the root of the
round's Merkle tree (seal_verify.MERKLE_RULE).

Additive only: a column that may stay empty; older containers ignore it (and stamp no rounds: SEAL_MERKLE is off
until after the deploy).
"""
import sqlalchemy as sa
from alembic import op

revision = "0073"
down_revision = "0072"
branch_labels = None
depends_on = None
ROLLING = True


def upgrade():
    bind = op.get_bind()
    if "path" not in {c["name"] for c in sa.inspect(bind).get_columns("seal_anchor")}:
        op.add_column("seal_anchor", sa.Column("path", sa.JSON(), nullable=True))


def downgrade():
    # SQLite copies the tables for this; the revision triggers name them and go first (app/data_revision.py)
    from app.data_revision import install, uninstall

    uninstall(op.get_bind())
    with op.batch_alter_table("seal_anchor") as batch:
        batch.drop_column("path")
    install(op.get_bind())
