"""Mails ohne Doppelte, Stand einmal täglich, Indizes auf Fremdschlüsseln

Revision ID: 0033
Revises: 0032
Create Date: 2026-10-10 10:00:00

Audit 2026-10-09b. E1: one row per address and survey on the invitation lists (uq_survey_invite_survey_email) and at
most one waiting mail per list entry (uq_mail_outbox_waiting_invite, partial: unsent rows only), so two requests at
the same moment cannot put an address on the list twice or mail it twice. Rows such a race left behind go first: of
the same address the opted-out row stays (it keeps the address off the list), else the oldest, with its waiting mails;
of two waiting mails of one entry the older stays. E7: survey_invite.pending_status, "begonnen"/"teilgenommen" taken
into the list once a day. P10/P11: indexes on the foreign keys that had none (deleting an account or a survey looked
through whole tables on PostgreSQL) and on feedback.status (counted on every researcher page).

It adds a column that may stay empty and indexes; the rows it removes are doubles the old code would not have made
on purpose and reads like any other missing row. So the deploy rolls. Indexes already there (built by create_all
from the models of this version) are left as they are.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0033'
down_revision = '0032'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other, and the one not yet restarted runs the
# old code against the new schema for a moment (deploy/docker/restart.sh, CLAUDE.md). Dropping or renaming: False.
ROLLING = True

WAITING = "sent_at IS NULL AND invite_id IS NOT NULL"

FK_INDEXES = [
    ('auth_token', 'created_by_id'), ('construct', 'created_by_id'), ('sign_in_grant', 'granted_by_id'),
    ('sign_in_orcid', 'added_by_id'), ('site_setting', 'changed_by_id'), ('survey', 'created_by_id'),
    ('user_login', 'status_changed_by_id'), ('workgroup', 'created_by_id'), ('construct_citation', 'added_by_id'),
    ('edit_mark', 'user_id'), ('edit_step', 'user_id'), ('experiment_grant', 'created_by_id'),
    ('feedback', 'survey_id'), ('feedback', 'user_id'), ('feedback', 'status'), ('preview_comment', 'author_id'),
    ('preview_comment', 'handled_by_id'), ('research_project', 'survey_id'), ('survey_group', 'added_by_id'),
    ('survey_label', 'created_by_id'), ('survey_member', 'invited_by_id'), ('survey_member', 'user_id'),
    ('theme_group_grant', 'created_by_id'), ('workgroup_member', 'user_id'), ('workgroup_member', 'invited_by_id'),
]


def _indexes(table):
    return {i['name'] for i in sa.inspect(op.get_bind()).get_indexes(table)}


def _remove_doubles():
    bind = op.get_bind()
    # survey_invite: per (survey, address) keep the opted-out row if there is one, else the oldest
    rows = bind.execute(sa.text("SELECT id, survey_id, email_hash, status FROM survey_invite ORDER BY id")).all()
    keep, gone = {}, []
    for row_id, survey_id, hashed, status in rows:
        key = (survey_id, hashed)
        if key not in keep:
            keep[key] = (row_id, status)
        elif status == 'optout' and keep[key][1] != 'optout':
            gone.append(keep[key][0])
            keep[key] = (row_id, status)
        else:
            gone.append(row_id)
    for start in range(0, len(gone), 500):
        part = gone[start:start + 500]
        bind.execute(sa.text("DELETE FROM mail_outbox WHERE sent_at IS NULL AND invite_id IN :ids")
                     .bindparams(sa.bindparam('ids', expanding=True)), {'ids': part})
        bind.execute(sa.text("DELETE FROM survey_invite WHERE id IN :ids")
                     .bindparams(sa.bindparam('ids', expanding=True)), {'ids': part})
    # mail_outbox: one waiting mail per entry, the older
    bind.execute(sa.text(f"DELETE FROM mail_outbox WHERE {WAITING} AND id NOT IN "
                         f"(SELECT min(id) FROM mail_outbox WHERE {WAITING} GROUP BY invite_id)"))


def upgrade():
    have = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('survey_invite')}
    if 'pending_status' not in have:
        with op.batch_alter_table('survey_invite', schema=None) as batch_op:
            batch_op.add_column(sa.Column('pending_status', sa.String(length=10), nullable=True))
    _remove_doubles()
    if 'uq_survey_invite_survey_email' not in _indexes('survey_invite'):
        op.create_index('uq_survey_invite_survey_email', 'survey_invite', ['survey_id', 'email_hash'], unique=True)
    if 'uq_mail_outbox_waiting_invite' not in _indexes('mail_outbox'):
        op.create_index('uq_mail_outbox_waiting_invite', 'mail_outbox', ['invite_id'], unique=True,
                        sqlite_where=sa.text(WAITING), postgresql_where=sa.text(WAITING))
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    for table, column in FK_INDEXES:
        name = f'ix_{table}_{column}'
        if table not in tables:
            continue  # a schema made from the models after 0091 has no theme_group_grant any more
        if name not in _indexes(table):
            op.create_index(name, table, [column], unique=False)


def downgrade():
    for table, column in FK_INDEXES:
        op.drop_index(f'ix_{table}_{column}', table_name=table)
    op.drop_index('uq_mail_outbox_waiting_invite', table_name='mail_outbox')
    op.drop_index('uq_survey_invite_survey_email', table_name='survey_invite')
    with op.batch_alter_table('survey_invite', schema=None) as batch_op:
        batch_op.drop_column('pending_status')
