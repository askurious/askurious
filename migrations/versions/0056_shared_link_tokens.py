"""Hash public dashboard and quota tokens; keep an encrypted copy for authorised display.

Old URLs remain valid. Old application code cannot look up the new hashes, so this change is not rolling.
"""
from alembic import op
import sqlalchemy as sa

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None
ROLLING = False


def upgrade():
    from app.secrets_box import encrypt
    from app.shared_tokens import digest

    connection = op.get_bind()
    for table in ("board_share", "quota_share"):
        if "token_box" not in {column["name"] for column in sa.inspect(connection).get_columns(table)}:
            op.add_column(table, sa.Column("token_box", sa.Text(), nullable=True))
        rows = connection.execute(sa.text(f"SELECT id, token FROM {table} WHERE token_box IS NULL OR token_box='' ")).mappings()
        for row in rows:
            token = row["token"]
            connection.execute(sa.text(f"UPDATE {table} SET token=:hash, token_box=:box WHERE id=:id"),
                               {"hash": digest(token), "box": encrypt(token), "id": row["id"]})


def downgrade():
    from app.secrets_box import decrypt

    connection = op.get_bind()
    for table in ("board_share", "quota_share"):
        rows = connection.execute(sa.text(f"SELECT id, token_box FROM {table}")).mappings()
        for row in rows:
            token = decrypt(row["token_box"])
            if not token:
                raise RuntimeError("Cannot restore a shared link without its encryption key")
            connection.execute(sa.text(f"UPDATE {table} SET token=:token WHERE id=:id"), {"token": token, "id": row["id"]})
        with op.batch_alter_table(table) as batch:
            batch.drop_column("token_box")
