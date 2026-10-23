"""Documented image rights and explicit permission for public archival distribution.

Existing images deliberately have no confirmation. Additive, nullable JSON; no backfilled permission.
"""
import sqlalchemy as sa
from alembic import op

revision = "0078"
down_revision = "0077"
branch_labels = None
depends_on = None
ROLLING = True


def upgrade():
    if "rights" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("asset")}:
        op.add_column("asset", sa.Column("rights", sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table("asset") as batch:
        batch.drop_column("rights")
