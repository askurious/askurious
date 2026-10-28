"""Kodierlinks: die Tabelle coding_link wird nicht mehr gelesen

Revision ID: 0105
Revises: 0104
Create Date: 2026-10-28 09:50:00

Changes nothing in the database. It marks the code that no longer maps the table coding_link (the coding links of
2026-10-27, only made to try out, Ralf 2026-10-28; a person without an account codes by an interviewer link with
"darf kodieren"). 0106 drops the table once this code runs everywhere (DROPS_UNUSED_SINCE = "0105").
"""


revision = '0105'
down_revision = '0104'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other (deploy/docker/restart.sh, CLAUDE.md).
ROLLING = True


def upgrade():
    pass


def downgrade():
    pass
