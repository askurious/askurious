"""The survey material as PDF (app/study_material.py; concept "Vereinheitlichung der Befragungsexporte", E10 A and C):
the questionnaire as participants see it and with notes, deterministic, in the deposit, without protected wording
there."""
import io

from pypdf import PdfReader

from app import packages, study_material, zenodo
from app.extensions import db
from app.models import Item, Survey
from tests.conftest import add, fill_consent
from tests.instrument_permissions import grant_instrument


def _text(body):
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(body)).pages)


def _survey(app, client):
    fill_consent(client)
    add(client, "single_choice")
    add(client, "likert")
    with app.app_context():
        s = db.session.get(Survey, 1)
        choice = db.session.scalar(db.select(Item).filter_by(type="single_choice"))
        likert = db.session.scalar(db.select(Item).filter_by(type="likert"))
        texts = dict(choice.texts or {})
        texts[s.default_language] = {**texts.get(s.default_language, {}), "question": "Wie oft fahren Sie Rad?"}
        choice.texts, choice.code, choice.answer_mode = texts, "rad", "required"
        likert.code = "einstellung"
        likert.config = {**likert.config, "randomize": True,
                         "when": {"item": choice.id, "op": "eq", "value": (choice.config.get("options") or [{}])[0].get("code", "1")}}
        db.session.commit()


def test_two_parts_with_notes_and_the_same_bytes_every_time(app, client, survey):
    _survey(app, client)
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        first, second = study_material.build(s, "de"), study_material.build(s, "de")
    assert first[:5] == b"%PDF-" and first == second  # deterministic: the same questionnaire gives the same bytes
    text = _text(first)
    assert "Teil 1: Fragebogen" in text and "Teil 2: Fragebogen mit Anmerkungen" in text
    assert "1. Wie oft fahren Sie Rad? *" in text  # numbered, required with *
    assert "Variable: rad" in text and "Antwort: Pflicht" in text
    assert "Reihenfolge zufällig" in text and "Nur gezeigt, wenn" in text


def test_download_and_english_frame(app, client, survey):
    _survey(app, client)
    r = client.get("/surveys/1/export/survey-material.pdf")
    assert r.status_code == 200 and r.mimetype == "application/pdf" and r.data[:5] == b"%PDF-"
    assert "Teil 1: Fragebogen" in _text(r.data)
    with app.test_request_context():
        assert "Part 2: Questionnaire with notes" in _text(study_material.build(db.session.get(Survey, 1), "en"))
    page = client.get("/surveys/1/data").data.decode()
    assert "/surveys/1/export/survey-material.pdf" in page and "Befragungsmaterial (PDF)" in page


def test_in_the_prereg_and_the_deposit_without_protected_wording(app, client, survey):
    from app import prereg

    grant_instrument(client.application, "gdms")
    fill_consent(client)
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "construct:gdms"})
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        item = db.session.scalar(db.select(Item).filter_by(type="likert"))
        words = [w for w in (item.t(s.default_language).get("statements") or {}).values() if w][:2]
        assert words
        files = dict(zenodo.package(s, require_public_image_rights=False))
        public = _text(files[packages.STUDY_MATERIAL])
        assert "Wording not included" in public and not any(w[:40] in public for w in words)
        own = _text(study_material.build(s, "de"))
        assert any(w[:30] in own.replace("\n", " ") for w in words)
        name = prereg.freeze(s, lang="de")
        import zipfile

        with zipfile.ZipFile(prereg.path(s, name)) as z:
            assert packages.STUDY_MATERIAL in z.namelist()


def test_prereg_in_the_seal_chain_and_compared_with_the_field(app, client, survey, monkeypatch):
    """E10 B: the SHA-256 of the frozen ZIP is an entry of the seal chain, written with the start; the export page
    compares the frozen survey file with the survey as it runs."""
    from app import prereg, seal
    from tests.exports.test_seal import sealed_survey

    monkeypatch.setattr(seal, "anchor_in_background", lambda: None)
    sealed_survey(app, client)
    client.post("/surveys/1/close")
    client.post("/surveys/1/reopen", data={"confirm": "1"})
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        entries = seal.entries(s.uid)
        assert [e.kind for e in entries][-2:] == ["prereg", "fieldstarted"]
        name, version, _, digest = prereg.listing(s)[0]
        assert version == s.qversion and entries[-2].content == digest and entries[-2].reason == name
        assert prereg.compare(s, name) == (0, [])
        assert not seal.check(s)["problems"]
    assert "unverändert seit dem Einfrieren" in client.get("/surveys/1/data").data.decode()
    with app.app_context():
        item = db.session.scalar(db.select(Item).filter(Item.type != "consent").order_by(Item.id.desc()))
        texts = {k: dict(v) for k, v in (item.texts or {}).items()} or {"de": {}}
        for t in texts.values():
            t["question"] = "Geänderter Wortlaut im Feld"
        item.texts = texts
        db.session.commit()
        count, said = prereg.compare(db.session.get(Survey, 1), name)
        assert count == 1 and item.code in said[0]
    assert "1 Unterschied(e) seit dem Einfrieren" in client.get("/surveys/1/data").data.decode()


def test_note_colours_come_from_the_researcher_pages_and_are_readable():
    """Design check of 2026-10-24 (thread "Data Colada 140"): the notes take the tokens of app.css, 4.5 : 1 on white,
    and random orders do not take the violet of AI."""
    import re

    from app import theme

    css = open("app/static/app.css", encoding="utf-8").read()
    block = re.search(r":root\s*\{(.*?)\n\}", css, re.S).group(1)
    for role, token in study_material.ROLES.items():
        value = study_material.colours()[role]
        assert re.search(rf"{token}:\s*{value}\b", block), (role, token, value)
        assert theme.contrast(value, "#ffffff") >= 4.5, role
    assert study_material.colours()["random"] not in block.split("--ai:")[1][:12]


def test_part_one_without_codes_and_ends_by_name(app, client, survey):
    """Part 1 shows options as participants see them (no answer codes); an end names its triggers once, by name."""
    fill_consent(client)
    add(client, "federal_state")
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        text = _text(study_material.build(s, "de"))
        part1, part2 = text.split("Teil 2: Fragebogen mit Anmerkungen")
        assert "( ) Bayern" in part1 and "[9]" not in part1 and "9 Bayern" not in part1
        assert "( ) Bayern [9]" in part2
        assert "\u25a0" not in text  # the legend draws its squares
        doc = study_material._Doc(s, "de", False)
        cells = [{"when": "cellfull", "quota": 1, "cells": [str(n)]} for n in range(24)]
        node = {"t": "end", "cases": [{"when": "nocell", "quotas": [1]}] + cells}
        assert study_material._end_cases(doc, node) == "Keine Teilquote passt, Teilquote voll (24 Zellen)"


def test_capital_after_a_colon_and_named_checks(app, client, survey):
    fill_consent(client)
    add(client, "text_input")
    with app.app_context():
        item = db.session.scalar(db.select(Item).filter_by(type="text_input"))
        item.config, item.answer_mode = {**item.config, "validation": "integer"}, "optional"
        db.session.commit()
    with app.test_request_context():
        text = _text(study_material.build(db.session.get(Survey, 1), "de"))
    assert "Antwort: Freiwillig" in text and "Prüfung der Eingabe: Ganze Zahl" in text
    assert "integer" not in text and "Schnellantworter (Speeder): Schneller" in text


def test_the_design_of_the_survey(app, client, survey):
    """An own design: its logo on the first page (the bytes stay the same every time); without one no image."""
    import io as _io

    from PIL import Image

    from app import media

    fill_consent(client)
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        assert b"/Subtype /Image" not in study_material.build(s, "de")
        png = _io.BytesIO()
        Image.new("RGB", (200, 50), (0, 84, 159)).save(png, format="PNG")
        saved = media.save_logo(s, png.getvalue())
        s.theme = {"preset": "custom", "colors": {"accent": "#00549f", "background": "#ffffff", "text": "#1d1d1b",
                                                  "surface": "#ffffff"},
                   "logo": {"file": saved["file"], "w": saved["w"], "h": saved["h"]}}
        db.session.commit()
        first = study_material.build(s, "de")
        assert b"/Subtype /Image" in first and first == study_material.build(s, "de")
        assert study_material._look(s)[0] == "#00549f"
