"""Standard wordings of elements are no longer stored in drafts

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-07 18:00:00

An element stored a copy of the standard wordings of its type in every language it was made with (buttons and
sentence of the consent, federal states, "Ich wohne nicht in Deutschland", "Keine davon", the dual response question,
"Option", ...). They are now taken in the language of the page when shown (app/item_texts.py, UI/UX audit 2026-10-07,
S9). This drops the copies that are still the standard wording (also an earlier one, e.g. "Fortsetzen") from the
elements of drafts only. Surveys that are running, paused or closed keep their texts as participants saw them; a
construct or linked element keeps its validated or frozen wording. Nothing else changes; running it twice changes
nothing more.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0017'
down_revision = '0016'
branch_labels = None
depends_on = None


def upgrade():
    from app import item_texts, items

    bind = op.get_bind()
    item = sa.table("item", sa.column("id", sa.Integer), sa.column("block_id", sa.Integer), sa.column("type", sa.String),
                    sa.column("config", sa.JSON), sa.column("texts", sa.JSON))
    block = sa.table("block", sa.column("id", sa.Integer), sa.column("survey_id", sa.Integer))
    survey = sa.table("survey", sa.column("id", sa.Integer), sa.column("status", sa.String),
                      sa.column("default_language", sa.String))
    rows = bind.execute(sa.select(item.c.id, item.c.type, item.c.config, item.c.texts, survey.c.default_language)
                        .select_from(item.join(block, item.c.block_id == block.c.id).join(survey, block.c.survey_id == survey.c.id))
                        .where(survey.c.status == "draft")).fetchall()
    for item_id, kind_key, config, texts, default in rows:
        kind = items.REGISTRY.get(kind_key)
        if kind is None or not isinstance(texts, dict) or (config or {}).get("construct"):
            continue
        cleaned = item_texts.strip(kind, texts, default or "de")
        if cleaned != texts:
            bind.execute(item.update().where(item.c.id == item_id).values(texts=cleaned))


def downgrade():
    # nothing to restore: an element without a stored standard wording shows it in every version that knows S9;
    # older versions show the texts of the default language in its place
    pass
