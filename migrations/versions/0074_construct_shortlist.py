"""The "Schnellbox" of the questionnaire editor (app/shortlist.py, konzept-konstrukt-schnellbox-2026-10-16.md): the
constructs an organisation's admins, or for the platform the site admins, put into the editor's list, in their order.

Additive only: one new table; older containers ignore it. No person is stored with an entry (who changed a list
goes into the security log, as other changes of administrators).
"""
import sqlalchemy as sa
from alembic import op

revision = "0074"
down_revision = "0073"
branch_labels = None
depends_on = None
ROLLING = True


def upgrade():
    if "construct_shortlist" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "construct_shortlist",
        sa.Column("id", sa.Integer(), primary_key=True),  # org_id without a foreign key: orgs.delete takes the rows
        sa.Column("org_id", sa.Integer(), nullable=True, index=True),
        sa.Column("key", sa.String(80), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )


def downgrade():
    op.drop_table("construct_shortlist")
