"""Organisation designs, second deploy (konzept-org-designs-2026-10-19.md, E; Ralf 2026-10-19): the RWTH templates go.

Every stored theme that still names the former template "rwth" or "rwth-dark" becomes the same look as an organisation
design (preset "org" with the template's name, tokens, fixed font and corners, logo position and colour suggestions,
frozen below): the themes of surveys, saved designs, and the states "Rückgängig" can put back (edit_step). Logo,
partners, scheme and the rest stay. The rendered CSS is the same as before (tests/ui/test_org_designs.py).

Then what 0090 stopped mapping goes: the columns organisation.themes (the "RWTH" tick), theme_light and theme_dark,
and the tables theme_grant and theme_group_grant (emptied by 0090).

Rolling once the database is at 0090 (DROPS_UNUSED_SINCE): the code of 0090 maps none of the dropped columns and
tables and renders preset "org". downgrade brings the columns and tables back empty and leaves the themes as they
are: the code of 0090 reads them.
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "0091"
down_revision = "0090"
branch_labels = None
depends_on = None
ROLLING = False
DROPS_UNUSED_SINCE = "0090"
DROPS = ("organisation.themes", "organisation.theme_light", "organisation.theme_dark", "theme_grant",
         "theme_group_grant")

# app/theme.py BUILTIN["rwth"] and ["rwth-dark"] on 2026-10-19, the last code that had them
RWTH = {'rwth': {'name': 'RWTH Aachen',
          'colors': {'accent': '#00549f', 'background': '#ffffff', 'text': '#000000', 'surface': '#ffffff'},
          'tokens': {'--muted': '#646567',
                     '--surface-2': '#eceded',
                     '--surface-3': '#eceded',
                     '--hover': '#eceded',
                     '--accent-soft': '#e8f1fa',
                     '--on-accent': '#ffffff',
                     '--focus-ring': '#00549f',
                     '--border': '#646567',
                     '--border-strong': '#646567',
                     '--line': '#cfd1d2',
                     '--track': '#cfd1d2',
                     '--track-edge': '#646567',
                     '--control-edge': '#646567',
                     '--error': '#cc071e',
                     '--error-soft': '#faebe3',
                     '--error-line': '#e69679',
                     '--nudge': '#000000',
                     '--nudge-soft': '#fff7ea',
                     '--nudge-line': '#f6a800',
                     '--good-soft': '#f2f7ec',
                     '--good-mid': '#ddebce',
                     '--good-line': '#b8d698',
                     '--bad-soft': '#faebe3',
                     '--bad-mid': '#f3cdbb',
                     '--bad-line': '#e69679',
                     '--star': '#f6a800',
                     '--star-hover': '#fabe50',
                     '--star-off': '#cfd1d2',
                     '--data': '#407fb7',
                     '--data-soft': '#c7ddf2',
                     '--data-strong': '#00549f',
                     '--crowd': '#646567',
                     '--own': '#a11035',
                     '--own-shadow': 'rgba(161, 16, 53, 0.28)',
                     '--ok': '#006165',
                     '--face-bg': '#ffffff',
                     '--face-edge': '#646567',
                     '--face-ink': '#000000',
                     '--face-on': '#8ebae5'},
          'fixed': {'font': 'grotesk', 'radius': 'square'},
          'logo_position': 'right',
          'swatches': ['#00549f',
                       '#000000',
                       '#e30066',
                       '#ffed00',
                       '#006165',
                       '#0098a1',
                       '#57ab27',
                       '#bdcd00',
                       '#f6a800',
                       '#cc071e',
                       '#a11035',
                       '#612158',
                       '#7a6fac']},
 'rwth-dark': {'name': 'RWTH Aachen dunkel',
               'colors': {'accent': '#8ebae5', 'background': '#000000', 'text': '#ffffff', 'surface': '#000000'},
               'tokens': {'--muted': '#9c9e9f',
                          '--on-accent': '#000000',
                          '--on-accent-veil': 'rgba(0, 0, 0, 0.7)',
                          '--focus-ring': '#8ebae5',
                          '--surface-2': '#000000',
                          '--surface-3': '#000000',
                          '--hover': '#000000',
                          '--accent-soft': '#000000',
                          '--border': '#646567',
                          '--border-strong': '#9c9e9f',
                          '--line': '#646567',
                          '--track': '#646567',
                          '--track-edge': '#9c9e9f',
                          '--control-edge': '#9c9e9f',
                          '--error': '#e69679',
                          '--error-soft': '#000000',
                          '--error-line': '#cc071e',
                          '--nudge': '#ffffff',
                          '--nudge-soft': '#000000',
                          '--nudge-line': '#f6a800',
                          '--ok': '#57ab27',
                          '--good-soft': '#000000',
                          '--good-mid': '#000000',
                          '--good-line': '#57ab27',
                          '--bad-soft': '#000000',
                          '--bad-mid': '#000000',
                          '--bad-line': '#cc071e',
                          '--star': '#f6a800',
                          '--star-hover': '#fabe50',
                          '--star-off': '#646567',
                          '--data': '#407fb7',
                          '--data-soft': '#00549f',
                          '--data-strong': '#8ebae5',
                          '--crowd': '#646567',
                          '--own': '#e5c5c0',
                          '--own-shadow': 'rgba(229, 197, 192, 0.28)',
                          '--face-bg': '#000000',
                          '--face-edge': '#9c9e9f',
                          '--face-ink': '#ffffff',
                          '--face-on': '#00549f',
                          '--fg': '#eceded',
                          '--chosen-edge': '#8ebae5'},
               'fixed': {'font': 'grotesk', 'radius': 'square'},
               'logo_position': 'right',
               'swatches': ['#00549f',
                            '#000000',
                            '#e30066',
                            '#ffed00',
                            '#006165',
                            '#0098a1',
                            '#57ab27',
                            '#bdcd00',
                            '#f6a800',
                            '#cc071e',
                            '#a11035',
                            '#612158',
                            '#7a6fac']}}

PAIR = {"rwth": "rwth-dark"}
OLD = tuple(RWTH)


def _load(value):
    if isinstance(value, (dict, list)) or value is None:
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def convert(doc):
    """`doc` with every theme in it that names an RWTH template as the same organisation design; (doc, changed)."""
    if isinstance(doc, list):
        rows = [convert(v) for v in doc]
        return [v for v, _ in rows], any(c for _, c in rows)
    if not isinstance(doc, dict):
        return doc, False
    changed = False
    out = {}
    for key, value in doc.items():
        out[key], c = convert(value)
        changed = changed or c
    if out.get("preset") in OLD and isinstance(out.get("colors"), dict):
        t = RWTH[out["preset"]]
        dark = out.get("colors_dark")
        out.update(preset="org", name=t["name"], tokens=dict(t["tokens"]), fixed=dict(t["fixed"]),
                   logo_position=t["logo_position"], swatches=list(t["swatches"]))
        out.update(t["fixed"])
        partner = PAIR.get(doc["preset"])
        if partner and out.get("scheme") == "auto" and isinstance(dark, dict) and dark == RWTH[partner]["colors"]:
            out["tokens_dark"] = dict(RWTH[partner]["tokens"])
        if isinstance(out.get("logo"), dict):
            out["logo"] = {**out["logo"], "position": t["logo_position"]}
        changed = True
    return out, changed


def _convert_column(bind, table, key, column):
    t = sa.table(table, sa.column(key, sa.Integer), sa.column(column, sa.JSON))
    rows = bind.execute(sa.select(t.c[key], sa.cast(t.c[column], sa.Text)).where(t.c[column].is_not(None))).all()
    for row_id, text in rows:
        if "rwth" not in (text or ""):
            continue
        doc, changed = convert(_load(text))
        if changed:
            bind.execute(t.update().where(t.c[key] == row_id).values({column: doc}))


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    for table, key, column in (("survey_theme", "survey_id", "data"), ("design", "id", "data"), ("edit_step", "id", "data")):
        if table in tables:
            _convert_column(bind, table, key, column)
    have = {c["name"] for c in sa.inspect(bind).get_columns("organisation")}
    gone = [c for c in ("themes", "theme_light", "theme_dark") if c in have]
    if gone:
        with op.batch_alter_table("organisation") as batch:
            for name in gone:
                batch.drop_column(name)
    for table in ("theme_group_grant", "theme_grant"):
        if table in tables:
            op.drop_table(table)


def downgrade():
    bind = op.get_bind()
    have = {c["name"] for c in sa.inspect(bind).get_columns("organisation")}
    with op.batch_alter_table("organisation") as batch:
        for name, kind in (("themes", sa.JSON()), ("theme_light", sa.String(40)), ("theme_dark", sa.String(40))):
            if name not in have:
                batch.add_column(sa.Column(name, kind, nullable=True))
    tables = set(sa.inspect(bind).get_table_names())
    if "theme_grant" not in tables:
        op.create_table(
            "theme_grant",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("audience", sa.String(length=40), nullable=False),
            sa.Column("allowed", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["user.id"], name=op.f("theme_grant_user_id_fkey"), ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id", name=op.f("theme_grant_pkey")),
            sa.UniqueConstraint("user_id", "audience", name=op.f("theme_grant_user_id_audience_key")),
        )
        op.create_index(op.f("ix_theme_grant_user_id"), "theme_grant", ["user_id"], unique=False)
    if "theme_group_grant" not in tables:
        op.create_table(
            "theme_group_grant",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("group_id", sa.Integer(), nullable=False),
            sa.Column("audience", sa.String(length=40), nullable=False),
            sa.Column("created_by_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["group_id"], ["workgroup.id"], name=op.f("theme_group_grant_group_id_fkey"),
                                    ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["created_by_id"], ["user.id"], name=op.f("theme_group_grant_created_by_id_fkey"),
                                    ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id", name=op.f("theme_group_grant_pkey")),
            sa.UniqueConstraint("group_id", "audience", name=op.f("theme_group_grant_group_id_audience_key")),
        )
        op.create_index(op.f("ix_theme_group_grant_group_id"), "theme_group_grant", ["group_id"], unique=False)
        op.create_index(op.f("ix_theme_group_grant_created_by_id"), "theme_group_grant", ["created_by_id"], unique=False)
