"""Data-pool revisions in shards: saving participants of one survey no longer wait for each other (audit 2026-10-15,
T05-N-1). The response triggers now count in response_revision (one row per pool and id modulo SHARDS) instead of the
survey's row and response_pool_revision.

Additive: a new table and replaced trigger functions. Containers with the old code read survey.data_rev and
response_pool_revision, which stand still from now on; their caches of results may stay old until they restart
with the new code a moment later (only during the deploy).
"""
import sqlalchemy as sa
from alembic import op

revision = "0064"
down_revision = "0061"
branch_labels = None
depends_on = None
ROLLING = True


def upgrade():
    from app.data_revision import install

    if "response_revision" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table("response_revision",
                        sa.Column("survey_id", sa.Integer(), primary_key=True),
                        sa.Column("source", sa.String(10), primary_key=True),
                        sa.Column("status", sa.String(20), primary_key=True),
                        sa.Column("shard", sa.Integer(), primary_key=True),
                        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="0"),
                        sa.Column("nonce", sa.String(32), nullable=False, server_default=""))
    install(op.get_bind())


def downgrade():
    from app.data_revision import install, uninstall

    uninstall(op.get_bind())
    op.drop_table("response_revision")
    install(op.get_bind())  # the questionnaire triggers again; the data ones need the code of 0058
