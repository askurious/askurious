"""Server-side receipts for participation integrity (audit SP13, package C)."""
from alembic import op
import sqlalchemy as sa

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None
ROLLING = True


def upgrade():
    if "browser_receipt" in sa.inspect(op.get_bind()).get_table_names():
        # Old schema-repair migrations may have removed a future table's foreign key.
        if not sa.inspect(op.get_bind()).get_foreign_keys("browser_receipt"):
            with op.batch_alter_table("browser_receipt") as batch:
                batch.create_foreign_key("browser_receipt_survey_id_fkey", "survey", ["survey_id"], ["id"], ondelete="CASCADE")
        return
    op.create_table("browser_receipt",
        sa.Column("survey_id", sa.Integer(), sa.ForeignKey("survey.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("scope", sa.String(80), primary_key=True),
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(), nullable=False))
    op.create_index("ix_browser_receipt_expires_at", "browser_receipt", ["expires_at"])


def downgrade():
    op.drop_table("browser_receipt")
