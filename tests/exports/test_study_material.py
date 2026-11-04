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
    assert "Aussagen in zufälliger Reihenfolge" in text and "Nur gezeigt, wenn" in text


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


# --- randomised surveys: part 3, images, text (concept "Befragungsmaterial mit sichtbarer Randomisierung") ---------


GAIN = "**Meldung**\n\nDas System hat **90 von 100** Fällen erkannt, und das Team sieht darin einen großen Gewinn für alle."
LOSS = "**Meldung**\n\nDas System hat **10 von 100** Fällen übersehen, und das Team sieht darin ein erhebliches Risiko für alle."
CONTROL = "**Meldung**\n\nDas System wird in mehreren Kliniken erprobt."


def _randomised(app, client, texts=(GAIN, LOSS, CONTROL), image=False):
    """Three blocks with one info element each in a randomisation cond1; the blocks stay in the flow's order."""
    from app import flow
    from app.models import Asset, Block

    fill_consent(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        lang = s.default_language
        ids = []
        for n, words in enumerate(texts):
            block = Block(survey=s, position=10 + n, titles={lang: f"Fassung {n + 1}"}, show_title=True)
            db.session.add(block)
            db.session.flush()
            config = {"source": "ai", "prompt": words, "size": "1536x1024"} if image else {}
            item = Item(block=block, type="image" if image else "info", code=f"V{n + 1}", answer_mode="optional", position=1,
                        config=config, texts={lang: {"alt": "Eine Seite"} if image else {"question": words}})
            db.session.add(item)
            if image:
                db.session.flush()
                db.session.add(Asset(item_id=item.id, filename="fehlt.webp", source="ai", prompt=words, model="ein-modell",
                                     status="approved"))
            ids.append(block.id)
        rest = [c for c in flow.tree(s).get("children", []) if c.get("block") not in ids]
        s.flow = {"t": "root", "children": rest + [
            {"t": "rand", "id": "r1", "code": "cond1", "name": "Rahmung", "k": 1, "balanced": True, "shuffle": False,
             "children": [{"t": "block", "block": b} for b in ids]}]}
        db.session.commit()


def _pages(body):
    return [page.extract_text().replace("\n", " ") for page in PdfReader(io.BytesIO(body)).pages]


def _parts(body):
    """(part 1, part 3, part 2) of the text of a PDF: the two with what a test compares first."""
    text = " ".join(_pages(body))
    first, rest = text.split("Teil 2: Fragebogen mit Anmerkungen")
    notes, conditions = rest.split("Teil 3: Alle Bedingungen")
    return first, conditions, notes


def test_line_breaks_are_no_question_marks_and_formatting_stays(app, client, survey):
    """Absätze standen als "??" im PDF: no font has the line break, so pdf_fonts.runs took it for a missing letter."""
    from app import pdf_fonts

    assert pdf_fonts.markup("eins\n\nzwei") == "eins\n\nzwei"
    _randomised(app, client)
    with app.test_request_context():
        body = study_material.build(db.session.get(Survey, 1), "de")
    text = " ".join(_pages(body))
    assert "??" not in text
    assert "<b>" not in text and "**" not in text


def test_part_1_is_one_path_and_says_so_part_3_shows_all(app, client, survey):
    _randomised(app, client)
    with app.test_request_context():
        first, middle, last = _parts(study_material.build(db.session.get(Survey, 1), "de"))
    assert "90 von 100" in first and "10 von 100" not in first and "mehreren Kliniken" not in first
    assert "Dies ist eine von 3 möglichen Varianten" in first and "cond1 = A: Fassung 1" in first
    assert "90 von 100" in middle and "10 von 100" in middle and "mehreren Kliniken" in middle
    assert "Wert in Spalte cond1" in middle and "Fassung 2" in middle and "1/3" in middle and "ausbalanciert" in middle
    assert all(w in last for w in ("90 von 100", "10 von 100", "mehreren Kliniken", "cond1 = C: Fassung 3"))


def test_without_a_randomisation_nothing_changes_in_the_parts(app, client, survey):
    _survey(app, client)
    with app.test_request_context():
        text = _text(study_material.build(db.session.get(Survey, 1), "de"))
    assert "Teil 3" not in text and "Varianten" not in text and "Aussagen in zufälliger Reihenfolge" in text


def test_every_way_of_comparing_builds_and_gives_the_same_bytes_again(app, client, survey):
    from app import study_conditions

    _randomised(app, client)
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        for mode in study_conditions.MODES:
            one = study_material.build(s, "de", compare=mode)
            assert one == study_material.build(s, "de", compare=mode), mode
            assert "Unabhängig" not in _parts(one)[1], mode  # there is only one randomisation
            assert "Part 3: All conditions" in _text(study_material.build(s, "en", compare=mode)), mode
        inline = _parts(study_material.build(s, "de", compare="inline"))[1]
        assert "[A" in inline and "Gemeinsam ist, was in allen vorkommt" in inline
        assert "90 von 100" in _parts(study_material.build(s, "de", compare="full"))[1]
    assert client.get("/surveys/1/export/survey-material.pdf?compare=inline").data[:5] == b"%PDF-"
    assert client.get("/surveys/1/export/survey-material.pdf?compare=unsinn").status_code == 200


def test_the_export_page_offers_the_views_only_for_a_randomised_survey(app, client, survey):
    fill_consent(client)
    page = client.get("/surveys/1/data").data.decode()
    assert 'name="compare"' not in page and "/surveys/1/export/survey-material.pdf" in page
    _randomised(app, client)
    page = client.get("/surveys/1/data").data.decode()
    assert page.count('name="compare"') == 4 and 'value="marked" checked' in page and "Vollständig mit Hervorhebung" in page
    assert "Bedingungen darstellen" in page and "nebeneinander" in page
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        bare = _parts(study_material.build(s, "de", compare="full"))[1]
    assert "Die Fassungen stehen unverändert" in bare and "berechnet" not in bare
    with app.test_request_context():
        highlighted = _parts(study_material.build(db.session.get(Survey, 1), "de", compare="marked"))[1]
    assert "nur in dieser Bedingung" in highlighted and "90 von 100" in highlighted


def test_columns_of_more_conditions_than_fit_stand_marked_one_below_the_other(app, client, survey):
    words = tuple(f"Der gleiche Anfang steht hier immer. Fassung {n} ist anders." for n in range(5))
    _randomised(app, client, words)
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        columns, full = study_material.build(s, "de", compare="columns"), study_material.build(s, "de", compare="full")
    assert b"".join(columns.split()) != b"".join(full.split()) and "nur in dieser Bedingung" in _parts(columns)[1]


def test_more_levels_than_fit_side_by_side_fall_back(app, client, survey):
    words = tuple(f"Der gleiche Anfang steht hier immer. Fassung {n} ist anders." for n in range(6))
    _randomised(app, client, words)
    with app.test_request_context():
        middle = _parts(study_material.build(db.session.get(Survey, 1), "de", compare="columns"))[1]
    assert all(f"cond1 = {letter}:" in middle for letter in "ABCDEF")


def test_the_same_text_everywhere_is_said_once(app, client, survey):
    _randomised(app, client, ("Für alle gleich.", "Für alle gleich."))
    with app.test_request_context():
        middle = _parts(study_material.build(db.session.get(Survey, 1), "de"))[1]
    assert "In allen Bedingungen gleich" in middle and middle.count("Für alle gleich.") == 1


def test_withheld_wording_stays_out_of_the_comparison(app, client, survey, monkeypatch):
    from app import export

    _randomised(app, client)
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        ids = {i.id for b in s.blocks for i in b.items if i.code == "V1"}
        monkeypatch.setattr(export, "withheld", lambda _s: ids)
        text = _text(study_material.build(s, "en", public=True))
    assert "90 von 100" not in text and "Wording not included" in text


def test_images_with_prompt_and_model_in_both_versions_only_with_rights_in_the_public_one(app, client, survey, tmp_path):
    from PIL import Image

    from app import asset_rights
    from app.models import Asset

    _randomised(app, client, ("a calm newspaper page with green", "a calm newspaper page with red"), image=True)
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        missing = study_material.build(s, "de")
        assert "Bilddatei fehlt" in _text(missing) and "ein-modell" in _text(missing)
        for a in db.session.scalars(db.select(Asset)):
            path = asset_rights.path(s, a)
            import os

            os.makedirs(os.path.dirname(path), exist_ok=True)
            Image.new("RGB", (1200, 800), "#336699").save(path, "WEBP")
        own = study_material.build(s, "de")
        assert own == study_material.build(s, "de")  # the bytes stay the same with images in them
        assert sum(len(p.images) for p in PdfReader(io.BytesIO(own)).pages) >= 3
        text = " ".join(_pages(own))
        assert "KI-erzeugtes Bild; Modell: ein-modell" in text and "Prompts im Vergleich" in text
        public = study_material.build(s, "en", public=True)
        assert not any(p.images for p in PdfReader(io.BytesIO(public)).pages)
        words = " ".join(_pages(public))
        assert "no rights declared for the public archive" in words and "ein-modell" in words and "calm newspaper" in words


def test_an_image_not_approved_is_not_shown(app, client, survey):
    from app.models import Asset

    _randomised(app, client, ("eins zwei drei vier", "eins zwei drei fünf"), image=True)
    with app.test_request_context():
        for a in db.session.scalars(db.select(Asset)):
            a.status = "pending"
        db.session.commit()
        text = _text(study_material.build(db.session.get(Survey, 1), "de"))
    assert "Bild noch nicht freigegeben" in text


def test_level_colours_can_be_read_on_white():
    def luminance(colour):
        channels = [int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        r, g, b = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    assert all(1.05 / (luminance(c) + 0.05) >= 4.5 for c in study_material.LEVEL_COLOURS)


def test_combinations_multiply_and_levels_add():
    block = {"t": "block", "block": 1}
    rand = {"t": "rand", "k": 1, "children": [block, block, block]}
    assert study_material.combinations({"t": "root", "children": [block, rand, rand]}) == 9
    nested = {"t": "rand", "k": 1, "children": [rand, block]}  # 3 inside one level, 1 in the other
    assert study_material.combinations({"t": "root", "children": [nested]}) == 4
    assert study_material.combinations({"t": "root", "children": [{"t": "rand", "k": 2, "children": [block] * 4}]}) == 6
    assert study_material.combinations({"t": "root", "children": [{"t": "branch", "children": [rand]}, rand]}) == 9


def test_a_row_stays_whole_unless_it_is_taller_than_a_page(app):
    """A long prompt may run over the page; a short question must not break in two."""
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph

    style = getSampleStyleSheet()["BodyText"]
    short = study_material.page_table([[Paragraph("kurz", style)]], colWidths=[300])
    long = study_material.page_table([[Paragraph("<br/>".join(["Zeile"] * 120), style)]], colWidths=[300])
    assert not short.splitInRow and long.splitInRow


def test_a_prompt_longer_than_a_page_does_not_stop_the_export(app, client, survey):
    long = " ".join(f"Wort{n} steht hier" for n in range(900))
    _randomised(app, client, (long, long + " und noch eins"), image=True)
    with app.test_request_context():
        assert study_material.build(db.session.get(Survey, 1), "de")[:5] == b"%PDF-"


def test_every_page_shows_an_id_that_follows_the_content(app, client, survey):
    """Exports of different sessions must not be mixed up (Philipp 2026-11-04), and the same questionnaire still gives
    the same bytes: the ID is a fingerprint of the PDF, no date."""
    import re

    _randomised(app, client)
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        one, again = study_material.build(s, "de", compare="inline"), study_material.build(s, "de", compare="inline")
        other = study_material.build(s, "de", compare="columns")
    assert one == again
    ids = [re.findall(r"ID ([0-9a-f]{8})", page) for page in _pages(one)]
    assert all(found for found in ids) and len({found[0] for found in ids}) == 1
    assert re.search(r"ID ([0-9a-f]{8})", " ".join(_pages(other))).group(1) != ids[0][0]


def test_exports_nobody_chooses_for_are_full_and_a_download_is_highlighted(app, client, survey):
    """Packages, the pre-registration and its seal hold the stimuli themselves; a person starts from the highlighted
    view (Philipp 2026-11-04). The number of conditions does not matter."""
    _randomised(app, client, (GAIN, LOSS))
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        automatic = _parts(study_material.build(s, "de"))[1]
        package = dict(zenodo.package(s, require_public_image_rights=False))[packages.STUDY_MATERIAL]
    assert "Die Fassungen stehen unverändert" in automatic
    assert "The versions stand unchanged" in " ".join(_pages(package))
    assert 'value="marked" checked' in client.get("/surveys/1/data").data.decode()
    download = " ".join(_pages(client.get("/surveys/1/export/survey-material.pdf").data))
    assert "nur in dieser Bedingung" in download and "marked" in download


def test_the_parts_keep_their_numbers_with_a_randomisation(app, client, survey):
    """Part 2 is part 2 in every survey; the conditions come last and the start of part 1 points to them."""
    _randomised(app, client)
    with app.test_request_context():
        text = " ".join(_pages(study_material.build(db.session.get(Survey, 1), "de")))
    assert text.index("Teil 1: Fragebogen") < text.index("Teil 2: Fragebogen mit Anmerkungen") < text.index("Teil 3: Alle Bedingungen")
    assert "Alle Bedingungen stehen in Teil 3." in text and "Teil 1b" not in text
    assert "Teil 1: Fragebogen (1. von 3 Varianten)" in text


def test_the_footer_names_the_survey_by_its_code(app, client, survey):
    fill_consent(client)
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        text = _text(study_material.build(s, "de"))
        assert f"{s.title} ({s.code}) | Fassung" in text

