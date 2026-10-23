"""Stage 2b, first deploy (konto-schluessel-2b-vorschlag.md, all as recommended, Ralf 2026-10-18): the key columns
of before may be empty. user.orcid holds only an ORCID iD (an account without ORCID made from now on has none); the
columns `orcid` of the member, share and address book tables are no longer needed by the code, which knows people by
account (user_id) since stage 2a. Migration 0085 (a later deploy) drops them; it may roll once the database is at
0084, because then no running code reads them (DROPS_UNUSED_SINCE, deploy/docker/restart.sh).

Rolling: a column that may be empty is only a relaxation for the code before, which keeps writing it.
"""
import sqlalchemy as sa
from alembic import op

revision = "0084"
down_revision = "0083"
branch_labels = None
depends_on = None
ROLLING = True

TABLES = ("user", "survey_member", "workgroup_member", "organisation_member", "design_share", "design_editor",
          "construct_share", "contact")


def upgrade():
    bind = op.get_bind()
    for table in TABLES:
        if "orcid" not in {c["name"] for c in sa.inspect(bind).get_columns(table)}:
            continue  # a schema made from models after 0085
        with op.batch_alter_table(table) as batch:
            batch.alter_column("orcid", existing_type=sa.String(19), nullable=True)


def downgrade():
    # accounts and rows made without a key get one again: a "q-…" key, as the code before made them
    import secrets

    bind = op.get_bind()
    alphabet = "23456789abcdefghjkmnpqrstuvwxyz"
    user = sa.table("user", sa.column("id", sa.Integer), sa.column("orcid", sa.String))
    for (uid,) in bind.execute(sa.select(user.c.id).where(user.c.orcid.is_(None))).all():
        bind.execute(user.update().where(user.c.id == uid)
                     .values(orcid="q-" + "".join(secrets.choice(alphabet) for _ in range(16))))
    for table in TABLES[1:]:
        t = sa.table(table, sa.column("orcid", sa.String), sa.column("user_id", sa.Integer))
        match = sa.select(user.c.orcid).where(user.c.id == t.c.user_id).scalar_subquery()
        bind.execute(t.update().where(t.c.orcid.is_(None), t.c.user_id.is_not(None)).values(orcid=match))
        bind.execute(t.delete().where(t.c.orcid.is_(None)))
    for table in TABLES:
        with op.batch_alter_table(table) as batch:
            batch.alter_column("orcid", existing_type=sa.String(19), nullable=False)
