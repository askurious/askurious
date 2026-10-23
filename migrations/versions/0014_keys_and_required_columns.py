"""Fremdschlüssel und Pflichtspalten wie im Code: Abweichungen älterer Datenbanken beheben

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-06 14:00:00

A database made before the migrations was stamped straight to the newest version (app/migrations.py), so repairs
like 0002 never ran there, and Administration > Datenbank kept listing "Abweichungen vom Schema des Codes":
foreign keys add_missing_columns never made for a column it added later, keys of old manual changes that say
something else than the code (SQLite left them without a name), and two columns that may be empty in the database
but not in the code (survey.code, response.answers).

Upgrade brings every foreign key in line with KEYS, the keys of the models at this version: a key that differs or
that the models do not have is dropped, a missing one is made under the convention's name. A reference to a row
that no longer exists is set to NULL first where the column may be empty, as ON DELETE SET NULL would have done;
in a required column such a key is left out on PostgreSQL (it would refuse it) and named in the log. Then empty
survey codes get a new code and empty answers {} before both columns become required. Databases built by 0001
have all of this already; there nothing happens.

Downgrade changes nothing: the keys and NOT NULL belong to the schema of 0001.
"""
import logging
import secrets

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")

# table: ((column, referred table, ON DELETE), ...); every key refers to the id of its table
KEYS = {
    'ai_usage': (('user_id', 'user', 'CASCADE'),),
    'asset': (('item_id', 'item', None),),
    'auth_token': (('created_by_id', 'user', 'SET NULL'), ('user_id', 'user', 'CASCADE')),
    'block': (('survey_id', 'survey', None),),
    'board': (('survey_id', 'survey', 'CASCADE'),),
    'board_share': (('board_id', 'board', 'CASCADE'),),
    'construct': (('created_by_id', 'user', None),),
    'construct_citation': (('added_by_id', 'user', 'SET NULL'), ('construct_id', 'construct', 'CASCADE'), ('survey_id', 'survey', 'SET NULL')),
    'construct_group_share': (('construct_id', 'construct', 'CASCADE'), ('group_id', 'workgroup', 'CASCADE')),
    'construct_pick': (('construct_id', 'construct', 'CASCADE'), ('user_id', 'user', 'CASCADE')),
    'construct_share': (('construct_id', 'construct', 'CASCADE'),),
    'construct_submission': (('construct_id', 'construct', 'CASCADE'), ('user_id', 'user', 'SET NULL')),
    'construct_tag': (('construct_id', 'construct', None), ('tag_id', 'tag', None)),
    'contact': (('owner_id', 'user', None),),
    'contact_entry': (('survey_id', 'survey', None),),
    'coscine_connection': (('user_id', 'user', None),),
    'design': (('owner_id', 'user', 'CASCADE'),),
    'design_editor': (('design_id', 'design', 'CASCADE'),),
    'design_group_share': (('design_id', 'design', 'CASCADE'), ('group_id', 'workgroup', 'CASCADE')),
    'design_share': (('design_id', 'design', 'CASCADE'),),
    'edit_mark': (('survey_id', 'survey', 'CASCADE'), ('user_id', 'user', 'CASCADE')),
    'edit_step': (('survey_id', 'survey', 'CASCADE'), ('user_id', 'user', 'CASCADE')),
    'erasure': (('survey_id', 'survey', None),),
    'experiment_grant': (('created_by_id', 'user', 'SET NULL'), ('group_id', 'workgroup', 'CASCADE'), ('user_id', 'user', 'CASCADE')),
    'feedback': (('survey_id', 'survey', 'SET NULL'), ('user_id', 'user', None)),
    'item': (('block_id', 'block', None),),
    'live_question': (('survey_id', 'survey', 'CASCADE'),),
    'live_reaction': (('survey_id', 'survey', 'CASCADE'),),
    'login_session': (('user_id', 'user', 'CASCADE'),),
    'once_value': (('response_id', 'response', 'CASCADE'), ('survey_id', 'survey', 'CASCADE')),
    'preview_comment': (('author_id', 'user', 'SET NULL'), ('handled_by_id', 'user', 'SET NULL'), ('survey_id', 'survey', 'CASCADE')),
    'quota': (('survey_id', 'survey', 'CASCADE'),),
    'quota_cell': (('quota_id', 'quota', 'CASCADE'),),
    'quota_share': (('survey_id', 'survey', 'CASCADE'),),
    'research_project': (('survey_id', 'survey', 'SET NULL'), ('user_id', 'user', 'CASCADE')),
    'response': (('survey_id', 'survey', None),),
    'sign_in_grant': (('granted_by_id', 'user', 'SET NULL'), ('user_id', 'user', 'CASCADE')),
    'sign_in_orcid': (('added_by_id', 'user', 'SET NULL'),),
    'site_setting': (('changed_by_id', 'user', 'SET NULL'),),
    'split_session': (('survey_id', 'survey', None),),
    'survey': (('created_by_id', 'user', None),),
    'survey_group': (('added_by_id', 'user', 'SET NULL'), ('group_id', 'workgroup', 'CASCADE'), ('survey_id', 'survey', 'CASCADE')),
    'survey_label': (('created_by_id', 'user', 'SET NULL'), ('survey_id', 'survey', 'SET NULL')),
    'survey_member': (('invited_by_id', 'user', None), ('survey_id', 'survey', None), ('user_id', 'user', None)),
    'survey_pin': (('survey_id', 'survey', 'CASCADE'), ('user_id', 'user', 'CASCADE')),
    'survey_theme': (('survey_id', 'survey', 'CASCADE'),),
    'theme_grant': (('user_id', 'user', 'CASCADE'),),
    'theme_group_grant': (('created_by_id', 'user', 'SET NULL'), ('group_id', 'workgroup', 'CASCADE')),
    'translation_mark': (('survey_id', 'survey', 'CASCADE'),),
    'trashed_item': (('survey_id', 'survey', None),),
    'user_login': (('status_changed_by_id', 'user', 'SET NULL'), ('user_id', 'user', 'CASCADE')),
    'workgroup': (('created_by_id', 'user', 'SET NULL'),),
    'workgroup_member': (('group_id', 'workgroup', 'CASCADE'), ('invited_by_id', 'user', 'SET NULL'), ('user_id', 'user', 'SET NULL')),
    'zenodo_connection': (('user_id', 'user', None),),
}
# fk as in app/extensions.py NAMING; gives SQLite's keys without a name one, so batch mode can drop them
CONVENTION = {"fk": "%(table_name)s_%(column_0_N_name)s_fkey"}
CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # app/models.py random_code, as of this version


def _ondelete(value):
    value = (value or "").upper()
    return None if value in ("", "NO ACTION") else value


def _keys(connection, inspector, table):
    """(drop, add) for one table: the names of the keys to drop, the (column, referred, ondelete) to make."""
    want = set(KEYS.get(table, ()))
    drop, have = [], set()
    for fk in inspector.get_foreign_keys(table):
        key = None
        if len(fk["constrained_columns"]) == 1 and fk["referred_columns"] == ["id"]:
            key = (fk["constrained_columns"][0], fk["referred_table"], _ondelete(fk.get("options", {}).get("ondelete")))
        if key in want and key not in have:
            have.add(key)
        else:
            drop.append(fk["name"] or f"{table}_{'_'.join(fk['constrained_columns'])}_fkey")
    add = []
    nullable = {c["name"]: c["nullable"] for c in inspector.get_columns(table)}
    for column, referred, ondelete in sorted(want - have):
        if column not in nullable:
            continue
        orphans = (f'{column} IS NOT NULL AND {column} NOT IN (SELECT id FROM "{referred}")')
        if nullable[column]:
            connection.execute(sa.text(f'UPDATE "{table}" SET {column} = NULL WHERE {orphans}'))
        elif connection.dialect.name == "postgresql" and connection.execute(
                sa.text(f'SELECT 1 FROM "{table}" WHERE {orphans} LIMIT 1')).first():
            log.warning("Foreign key %s.%s not made: rows refer to %s rows that no longer exist", table, column, referred)
            continue
        add.append((column, referred, ondelete))
    return drop, add


def _fix_keys():
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    tables = set(inspector.get_table_names())
    for table in sorted(tables):
        if table == "alembic_version" or table.startswith("construct_fts"):
            continue
        drop, add = _keys(connection, inspector, table)
        if not drop and not add:
            continue
        if connection.dialect.name == "sqlite":
            with op.batch_alter_table(table, naming_convention=CONVENTION) as batch_op:
                for name in drop:
                    batch_op.drop_constraint(name, type_="foreignkey")
                for column, referred, ondelete in add:
                    batch_op.create_foreign_key(f"{table}_{column}_fkey", referred, [column], ["id"], ondelete=ondelete)
        else:
            for name in drop:
                op.drop_constraint(name, table, type_="foreignkey")
            for column, referred, ondelete in add:
                op.create_foreign_key(f"{table}_{column}_fkey", table, referred, [column], ["id"], ondelete=ondelete)
        log.warning("Foreign keys of %s brought in line with the code: %d dropped, %d made", table, len(drop), len(add))


def _nullable(inspector, table, column):
    return next(c["nullable"] for c in inspector.get_columns(table) if c["name"] == column)


def _new_code(taken):
    while True:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))
        if not code.isdigit() and code not in taken:
            taken.add(code)
            return code


def _fix_required():
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if _nullable(inspector, "survey", "code"):
        taken = {c for (c,) in connection.execute(sa.text("SELECT code FROM survey WHERE code IS NOT NULL"))}
        for (sid,) in connection.execute(sa.text("SELECT id FROM survey WHERE code IS NULL")).fetchall():
            connection.execute(sa.text("UPDATE survey SET code = :code WHERE id = :id"), {"code": _new_code(taken), "id": sid})
        with op.batch_alter_table('survey', schema=None) as batch_op:
            batch_op.alter_column('code', existing_type=sa.String(length=12), nullable=False)
    if _nullable(inspector, "response", "answers"):
        connection.execute(sa.text("UPDATE response SET answers = '{}' WHERE answers IS NULL"))
        with op.batch_alter_table('response', schema=None) as batch_op:
            batch_op.alter_column('answers', existing_type=sa.JSON(), nullable=False)


def upgrade():
    _fix_keys()
    _fix_required()


def downgrade():
    pass  # the keys and NOT NULL belong to the schema of 0001: going back keeps them
