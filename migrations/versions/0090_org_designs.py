"""Organisation designs instead of the RWTH tick (app/org_designs.py, konzept-org-designs-2026-10-19.md, Ralf 2026-10-19)

Revision ID: 0090
Revises: 0089
Create Date: 2026-10-19 11:00:00

Every organisation gets an organisation design, light and dark, made by its admins, and a template site admins may
upload as their starting point: four JSON columns on organisation. They are filled from what there was:

- an organisation with the tick "RWTH" (organisation.themes) gets the RWTH pair as template, and as its organisation
  design unless it had a default design of its own;
- a default design (theme_light/theme_dark: a built-in template or "design:<id>") becomes the organisation design, as a
  whole copy, so new surveys keep starting with the same look;
- a saved design that names the RWTH template becomes the same values as an organisation design (preset "org");
- the old grants theme_grant and theme_group_grant, which decide nothing since 2026-10-16, are emptied.

Only additions and data the old code reads the same way (it does not read the new columns; the participants' side
reads neither designs nor grants), so the deploy rolls. The values of the built-in templates are frozen here (BUILTIN)
because app/theme.py loses the RWTH ones with 0091. The columns themes, theme_light, theme_dark and the two grant
tables are no longer mapped from this revision on and are dropped by 0091 (DROPS_UNUSED_SINCE = "0090").
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "0090"
down_revision = "0089"
branch_labels = None
depends_on = None
ROLLING = True

COLUMNS = ("design_light", "design_dark", "template_light", "template_dark")

# app/theme.py BUILTIN on 2026-10-19 (name, colours, look, tokens, fixed font/corners, logo position, colour suggestions)
BUILTIN = {'askurious': {'name': 'Askurious',
               'colors': {'accent': '#2a5db0', 'background': '#ffffff', 'text': '#1d1d1b', 'surface': '#ffffff'}},
 'askurious-dark': {'name': 'Askurious dunkel',
                    'colors': {'accent': '#7ea6e6', 'background': '#141517', 'text': '#e7e5e0', 'surface': '#1c1e21'},
                    'tokens': {'--muted': '#a4a29c', '--on-accent': '#0e1a2e', '--error': '#f08d86'}},
 'rwth': {'name': 'RWTH Aachen',
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
                            '#7a6fac']},
 'papier': {'name': 'Papier',
            'colors': {'accent': '#a4442a', 'background': '#f4eee2', 'text': '#2a241c', 'surface': '#fffcf5'},
            'look': {'font': 'serif', 'radius': 'square', 'font_scale': 112}},
 'salbei': {'name': 'Salbei',
            'colors': {'accent': '#1f6f5c', 'background': '#e6efe9', 'text': '#17302a', 'surface': '#ffffff'},
            'look': {'font': 'rounded', 'radius': 'round', 'font_scale': 100},
            'tokens': {'--error-soft': '#fbeceb'}},
 'nachtblau': {'name': 'Nachtblau',
               'colors': {'accent': '#ff7f61', 'background': '#101a33', 'text': '#f3efe8', 'surface': '#17233f'},
               'look': {'font': 'atkinson', 'radius': 'soft', 'font_scale': 100}},
 'schwarz-gelb': {'name': 'Schwarz-Gelb',
                  'colors': {'accent': '#000000', 'background': '#ffffff', 'text': '#000000', 'surface': '#ffffff'},
                  'look': {'font': 'grotesk', 'radius': 'square', 'font_scale': 100},
                  'tokens': {'--accent-soft': '#ffe600',
                             '--on-accent': '#ffe600',
                             '--star': '#ffd200',
                             '--star-hover': '#ffe600',
                             '--hover': '#fff8b8'}},
 'retro': {'name': 'Retro',
           'colors': {'accent': '#1e7a3c', 'background': '#e9f3e1', 'text': '#113a1f', 'surface': '#f6fbf1'},
           'look': {'font': 'mono', 'radius': 'square', 'font_scale': 100},
           'tokens': {'--error-soft': '#f9e4e1'}},
 'retro-dark': {'name': 'Retro dunkel',
                'colors': {'accent': '#42e57a', 'background': '#0b1d12', 'text': '#b9f6c9', 'surface': '#11291b'},
                'look': {'font': 'mono', 'radius': 'square', 'font_scale': 100}}}


def _json(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return value


def _extras(key):
    t = BUILTIN[key]
    return {k: t[k] for k in ("tokens", "fixed", "logo_position", "swatches") if t.get(k)}


def as_org(key, org_id=None):
    """A built-in template as an organisation design: what it fixes written into it (app/theme.py, as_org)."""
    t = BUILTIN[key]
    look = t.get("look", {})
    data = {"v": 1, "preset": "org", "name": t["name"], "colors": dict(t["colors"]), "font": look.get("font", "system"),
            "font_scale": look.get("font_scale", 100), "radius": look.get("radius", "soft"), "scheme": "fixed",
            **_extras(key)}
    data.update(data.get("fixed", {}))
    if org_id is not None:
        data["org"] = org_id
    return data


def from_design(data, org_id=None):
    """A saved design (its theme) as an organisation design: its light side, without logo and partners."""
    data = _json(data) or {}
    key = data.get("preset")
    if key in BUILTIN:
        out = as_org(key, org_id)
        out.update({k: data[k] for k in ("font", "font_scale", "radius") if k in data and k not in out.get("fixed", {})})
        return out
    colors = data.get("colors") if isinstance(data.get("colors"), dict) else None
    if not colors:
        return None
    out = {"v": 1, "preset": "org", "name": "", "colors": colors, "scheme": "fixed",
           **{k: data[k] for k in ("font", "font_scale", "radius", "tokens", "fixed", "logo_position", "swatches")
              if k in data}}
    if org_id is not None:
        out["org"] = org_id
    return out


def convert_rwth(data):
    """A stored theme that names the RWTH template, as the same values with preset "org" (logo, partners and the
    rest kept); None if it names none."""
    data = _json(data)
    if not isinstance(data, dict) or data.get("preset") not in ("rwth", "rwth-dark"):
        return None
    key = data["preset"]
    out = {**data, "preset": "org", "name": BUILTIN[key]["name"], **_extras(key)}
    dark = data.get("colors_dark")
    if data.get("scheme") == "auto" and isinstance(dark, dict) and dark == BUILTIN["rwth-dark"]["colors"]:
        out["tokens_dark"] = dict(BUILTIN["rwth-dark"]["tokens"])
    return out


def upgrade():
    bind = op.get_bind()
    have = {c["name"] for c in sa.inspect(bind).get_columns("organisation")}
    for name in COLUMNS:
        if name not in have:
            op.add_column("organisation", sa.Column(name, sa.JSON(), nullable=True))
    tables = set(sa.inspect(bind).get_table_names())
    designs = {row[0]: (row[1], row[2]) for row in bind.execute(sa.text("SELECT id, name, data FROM design"))}

    def slot(key, org_id):
        if not key:
            return None
        if key in BUILTIN:
            return as_org(key, org_id)
        if key.startswith("design:") and key[7:].isdigit() and int(key[7:]) in designs:
            label, data = designs[int(key[7:])]
            out = from_design(data, org_id)
            if out is not None:
                out["name"] = (label or "Design")[:80]
            return out
        return None

    if {"themes", "theme_light", "theme_dark"} <= have:
        for org_id, themes, light, dark, done in bind.execute(sa.text(
                "SELECT id, themes, theme_light, theme_dark, design_light FROM organisation")).fetchall():
            if done is not None:
                continue  # run before
            rwth = "rwth" in (_json(themes) or [])
            values = {"dl": slot(light, org_id), "dd": slot(dark, org_id) if light else None, "tl": None, "td": None}
            if rwth:
                values.update(tl=as_org("rwth", org_id), td=as_org("rwth-dark", org_id))
                if values["dl"] is None:
                    values.update(dl=as_org("rwth", org_id), dd=as_org("rwth-dark", org_id))
            bind.execute(sa.text("UPDATE organisation SET design_light = :dl, design_dark = :dd, template_light = :tl, "
                                 "template_dark = :td WHERE id = :id").bindparams(
                *(sa.bindparam(k, type_=sa.JSON(none_as_null=True)) for k in values)), {"id": org_id, **values})
    for design_id, (_label, data) in designs.items():
        new = convert_rwth(data)
        if new is not None:
            bind.execute(sa.text("UPDATE design SET data = :data WHERE id = :id").bindparams(
                sa.bindparam("data", type_=sa.JSON())), {"id": design_id, "data": new})
    for table in ("theme_grant", "theme_group_grant"):
        if table in tables:
            bind.execute(sa.text(f"DELETE FROM {table}"))


def downgrade():
    # SQLite copies the tables for this; the revision triggers name them and go first (app/data_revision.py)
    from app.data_revision import install, uninstall

    uninstall(op.get_bind())
    with op.batch_alter_table("organisation") as batch_op:
        for name in COLUMNS:
            batch_op.drop_column(name)
    install(op.get_bind())
