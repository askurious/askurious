"""Shares of research projects with groups that no longer exist

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-07 22:00:00

A research project (AInstein) names the groups it is shared with by id in research_project.shares. Deleting a group
left that entry, and SQLite gave the id of a deleted last group to the next new one, whose members then had the
project's role (code review 2026-10-07, K1). Groups now keep their ids apart (models.IdFloor) and groups.remove takes
the group out of the shares. This drops the entries of groups that are gone already. Nothing else changes; running it
twice changes nothing more.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0019'
down_revision = '0018'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    project = sa.table("research_project", sa.column("id", sa.Integer), sa.column("shares", sa.JSON))
    group = sa.table("workgroup", sa.column("id", sa.Integer))
    groups = set(bind.execute(sa.select(group.c.id)).scalars())
    for pid, shares in bind.execute(sa.select(project.c.id, project.c.shares)).fetchall():
        if not isinstance(shares, list):
            continue
        kept = [s for s in shares if not (isinstance(s, dict) and s.get("kind") == "group" and s.get("group_id") not in groups)]
        if len(kept) != len(shares):
            bind.execute(project.update().where(project.c.id == pid).values(shares=kept))


def downgrade():
    # nothing to restore: the entries named groups that do not exist
    pass
