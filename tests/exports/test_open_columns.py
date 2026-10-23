"""Open answers (free text, exact positions, a day, postal codes) leave a public data package only with the tick
(export.open_columns). Each element type names its columns itself (ItemType.open_columns); code review of
2026-10-08, S5: the short text of the Side-by-Side-Matrix and the names of own groups of the card sort were missing
from a fixed list of types."""
import csv
import io

from app import export, items
from app.extensions import db
from app.items.base import ItemType
from app.models import Block, Item, Survey

# Element types without anything a person types in freely or that locates them: codes, numbers, orders, positions on
# a picture or in a grid, ids from the researcher's table (drill_down), sizes of the comparison group shown (feedback).
# A new type is either added here or names its columns in open_columns().
NO_FREE_TEXT = {
    "aca", "acbc", "assign", "calc", "conjoint", "consent", "cva", "discounting", "drill_down", "guide_question", "highlight", "iat",
    "iat_feedback", "image", "image_mark", "info", "likert", "maxdiff", "media", "page_break", "paired", "psm", "rating", "sample_compare",
    "sample_crowd", "sample_map", "scale_profile", "score_band", "semantic", "slider", "topic_map", "topic_profile",
}


def test_every_type_names_its_open_columns_or_has_none():
    undecided = [key for key, kind in items.REGISTRY.items()
                 if type(kind).open_columns is ItemType.open_columns and key not in NO_FREE_TEXT]
    assert not undecided, f"open_columns() fehlt oder Typ in NO_FREE_TEXT eintragen: {undecided}"
    assert not NO_FREE_TEXT - set(items.REGISTRY), "NO_FREE_TEXT nennt Typen, die es nicht gibt"


def _with_text(kind, item):
    """The settings under which the type stores free text (an option "Anderes: ...", a column "Kurztext" ...)."""
    c = item.config
    if item.type in ("single_choice", "multiple_choice") and c.get("options"):
        c["options"][0] = {**c["options"][0], "text": True}
    elif item.type == "side_by_side":
        c["columns"][1] = {**c["columns"][1], "kind": "text"}
        c["rows"][0]["code"] = "r1"
    elif item.type == "card_sort":
        c.update(mode="open", max_own=2)
    elif item.type == "map_marker":
        c["questions"] = [{"id": "q1", "kind": "text"}]
    elif item.type == "date_time":
        c["parts"] = "date"


def _all_types(app):
    """One element of every type in survey 1, with the settings that store free text."""
    with app.app_context():
        block = db.session.scalars(db.select(Block).filter_by(survey_id=1).order_by(Block.position)).all()[-1]
        for n, (key, kind) in enumerate(items.REGISTRY.items()):
            if key == "consent":
                continue  # survey 1 has its consent already
            item = Item(block_id=block.id, position=10 + n, type=key, code=f"t{n}", texts={"de": {}},
                        config=kind.default_config() or {})
            _with_text(kind, item)
            db.session.add(item)
        db.session.commit()


def test_every_text_column_of_the_codebook_is_an_open_answer(app, client, survey):
    """Whatever the codebook calls text or a postal code (typed, so it can name someone) is an open answer."""
    _all_types(app)
    with app.app_context():
        s = db.session.get(Survey, 1)
        opened = export.open_columns(s)
        book = export.codebook(s)
        text = {row[0] for row in book if row[2] in ("Text", "Postleitzahl")}
        assert text, "das Codebuch nennt keine Textspalten"
        missing = sorted(c for c in text if c not in opened and export.is_public_column(c))
        assert not missing, f"Textspalten ohne Häkchen veröffentlicht: {missing}"


def test_s5_short_text_and_own_group_names_stay_out_of_public_data(app, client, survey):
    with app.app_context():
        block = db.session.scalars(db.select(Block).filter_by(survey_id=1).order_by(Block.position)).all()[-1]
        sbs = Item(block_id=block.id, position=5, type="side_by_side", code="sbs", texts={"de": {}}, config={})
        sbs.config = sbs.kind.default_config()
        _with_text(sbs.kind, sbs)
        cs = Item(block_id=block.id, position=6, type="card_sort", code="cs", texts={"de": {}}, config={})
        cs.config = cs.kind.default_config()
        _with_text(cs.kind, cs)
        db.session.add_all([sbs, cs])
        db.session.commit()
        s = db.session.get(Survey, 1)
        sbs_text = [c for c, _ in sbs.kind.export_columns(sbs) if sbs.kind.lists_texts(sbs, c)]
        own = [c for c, _ in cs.kind.export_columns(cs) if "_own" in c]
        assert sbs_text and own == ["cs_own1", "cs_own2"]
        assert set(sbs_text) | set(own) <= export.open_columns(s)
        header = next(csv.reader(io.StringIO(export.to_csv(s, public=True).lstrip("﻿"))))
        assert not (set(sbs_text) | set(own)) & set(header)
        header = next(csv.reader(io.StringIO(export.to_csv(s, public=True, open_answers=True).lstrip("﻿"))))
        assert set(sbs_text) | set(own) <= set(header)
