"""Encrypted account work and pending address confirmation (AU-10/AU-11)."""
from alembic import op
import sqlalchemy as sa

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None
ROLLING = True


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "secret_box" not in {column["name"] for column in inspector.get_columns("auth_token")}:
        op.add_column("auth_token", sa.Column("secret_box", sa.Text(), nullable=True))
    if "account_job" not in inspector.get_table_names():
        op.create_table("account_job", sa.Column("id", sa.Integer(), primary_key=True),
                        sa.Column("payload_box", sa.Text(), nullable=False),
                        sa.Column("created_at", sa.DateTime(), nullable=False))
    if "ix_account_job_created_at" not in {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("account_job")}:
        op.create_index("ix_account_job_created_at", "account_job", ["created_at"])


def downgrade():
    op.drop_table("account_job")
    with op.batch_alter_table("auth_token") as batch:
        batch.drop_column("secret_box")
