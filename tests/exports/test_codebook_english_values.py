"""The codebook in English describes the values in English too (security and code audit 2026-10-13, CF-1): "0 = nicht
gewählt", "-77 = nicht gezeigt", "-98 = weiß nicht" and "1 = zugestimmt" stood in the English codebook; the SPSS
files read the German codebook and keep its words."""
from app import export
from app.extensions import db
from app.models import Item, Survey
from tests.conftest import add

GERMAN = ("nicht gewählt", "= gewählt", "nicht gezeigt", "weiß nicht", "zugestimmt", "abgelehnt", "angekreuzt",
          "Angabe gemacht")


def _values(rows):
    return " | ".join(str(r[3]) for r in rows)


def test_english_codebook_has_no_german_value_words(app, client, survey):
    add(client, "multiple_choice")
    add(client, "likert")
    with app.app_context():
        likert = db.session.scalar(db.select(Item).filter_by(type="likert"))
        likert.config = {**(likert.config or {}), "dont_know": True}
        db.session.commit()
        s = db.session.get(Survey, 1)
        english, german = _values(export.codebook(s, lang="en")), _values(export.codebook(s, lang="de"))
    assert "0 = not chosen; 1 = chosen" in english and "don't know" in english and "1 = agreed; 0 = declined" in english
    assert not [w for w in GERMAN if w in english], english
    assert "0 = nicht gewählt; 1 = gewählt" in german and "weiß nicht" in german     # German as before (SPSS reads it)


def test_value_words_of_element_types_follow_the_reader():
    """The element types' own codebook rows, e.g. the column "weiß nicht" of a date."""
    from types import SimpleNamespace

    from app import items

    date = SimpleNamespace(code="d1", config={}, cfg={})
    kind = items.get("date_time")
    assert kind.codebook_info(date, "d1_dk", "de", "en")[1] == "0 = answer given; 1 = don't know"
    assert kind.codebook_info(date, "d1_dk", "de", "de")[1] == "0 = Angabe gemacht; 1 = weiß nicht"
