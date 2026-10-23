"""Transactional data and questionnaire cache revisions (SP13 G)."""
from alembic import op
import sqlalchemy as sa
revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None
ROLLING = True


def upgrade():
    from app.data_revision import install
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("survey")}
    for name in ("data_rev", "structure_rev"):
        if name not in columns:
            op.add_column("survey", sa.Column(name, sa.BigInteger(), nullable=False, server_default="0"))
    for name in ("data_nonce", "structure_nonce"):
        if name not in columns:
            op.add_column("survey", sa.Column(name, sa.String(32), nullable=False, server_default=""))
    if "response_pool_revision" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table("response_pool_revision",
                        sa.Column("survey_id", sa.Integer(), sa.ForeignKey("survey.id", ondelete="CASCADE"), primary_key=True),
                        sa.Column("source", sa.String(10), primary_key=True),
                        sa.Column("status", sa.String(20), primary_key=True),
                        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="0"),
                        sa.Column("nonce", sa.String(32), nullable=False, server_default=""))
    elif not sa.inspect(op.get_bind()).get_foreign_keys("response_pool_revision"):
        with op.batch_alter_table("response_pool_revision") as batch:
            batch.create_foreign_key("response_pool_revision_survey_id_fkey", "survey", ["survey_id"], ["id"], ondelete="CASCADE")
    if "nonce" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("response_pool_revision")}:
        op.add_column("response_pool_revision", sa.Column("nonce", sa.String(32), nullable=False, server_default=""))
    install(op.get_bind())


def downgrade():
    from app.data_revision import uninstall
    uninstall(op.get_bind())
    op.drop_table("response_pool_revision")
    with op.batch_alter_table("survey") as batch:
        batch.drop_column("structure_nonce")
        batch.drop_column("data_nonce")
        batch.drop_column("structure_rev")
        batch.drop_column("data_rev")
