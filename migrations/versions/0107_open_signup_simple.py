"""Freie Anmeldung: nur noch „nur mit Einladung / jede Person“; Obergrenze und Warteliste werden nicht mehr gelesen

Revision ID: 0107
Revises: 0106
Create Date: 2026-10-29 11:30:00

Sign-in with ORCID has one switch since 2026-10-29 (Ralf): "only with an invitation" or "every person"; the limit and
the waiting list went (app/signin.py). Who should come into a particular organisation gets a registration link, which
can now be without a limit and without an end.

An account that came in by "every person" alone gets an admin's yes (sign_in_grant) from now on, so switching to
"only with an invitation" locks nobody out. This migration gives that yes to the accounts that rely on the switch now:
with "every person" every account with ORCID that has none yet; with "up to a limit" those that came in by the switch
(user_login.came_via 'open'), and the switch goes to "only with an invitation", as Ralf decided for that state. The
settings of the limit go.

The code of this revision no longer maps the table sign_in_wait and no longer reads the column user_login.came_via;
a later migration drops them (DROPS_UNUSED_SINCE = "0107", after the column is taken out of the models). downgrade leaves the yes given, it is what the accounts had.
"""
from alembic import op
import sqlalchemy as sa


revision = '0107'
down_revision = '0106'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other (deploy/docker/restart.sh, CLAUDE.md).
# The rows it changes are read by the researchers' sign-in alone (tests/platform/test_rolling_restart.py,
# ROLLING_ALLOWED).
ROLLING = True


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if 'site_setting' not in tables:
        return
    mode = bind.execute(sa.text("SELECT value FROM site_setting WHERE key = 'all_orcids'")).scalar()
    now = sa.func.now()
    grant = sa.table('sign_in_grant', sa.column('user_id', sa.Integer), sa.column('granted_by_id', sa.Integer),
                     sa.column('created_at', sa.DateTime))
    user = sa.table('user', sa.column('id', sa.Integer), sa.column('orcid', sa.String), sa.column('pending', sa.Boolean))
    login = sa.table('user_login', sa.column('user_id', sa.Integer), sa.column('came_via', sa.String))
    has = sa.select(grant.c.user_id).where(grant.c.user_id == user.c.id).exists()
    who = None
    if mode == '1':
        who = sa.select(user.c.id).where(user.c.orcid.is_not(None), user.c.pending.is_(False), ~has)
    elif mode == 'cap':
        who = (sa.select(user.c.id).join(login, login.c.user_id == user.c.id)
               .where(login.c.came_via == 'open', user.c.pending.is_(False), ~has))
    if who is not None:
        ids = [row[0] for row in bind.execute(who)]
        for i in ids:
            bind.execute(grant.insert().values(user_id=i, granted_by_id=None, created_at=now))
    setting = sa.table('site_setting', sa.column('key', sa.String), sa.column('value', sa.String))
    if mode == 'cap':
        bind.execute(setting.update().where(setting.c.key == 'all_orcids').values(value='0'))
    bind.execute(setting.delete().where(setting.c.key.in_(('open_limit', 'open_limit_said'))))


def downgrade():
    pass
