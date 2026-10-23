"""Audit "Daten und Berechnungen" 2026-10-10, package E (elements and changes in the field): N-2 to N-12, lists of
places like postal codes, and the sentence on the personal code in the consent (L-14)."""
import os
import tempfile

import pytest
from werkzeug.datastructures import MultiDict

from app import answer_lock, export, results
from app.extensions import db
from app.items.base import DONT_KNOW, AnswerError
from app.models import Item, Response, Survey
from tests.conftest import add, fill_consent
from tests.editor.test_answer_lock import _reopened_with_answers
from tests.items.test_card_sort_qsort import setup_cards, setup_q
from tests.items.test_image_mark import AREAS, setup as setup_mark, setup_hot
from tests.items.test_side_by_side import editor_form, parse as parse_sbs, setup as setup_sbs


def _real(app, answers, n=1):
    with app.app_context():
        for k in range(n):
            db.session.add(Response(survey_id=1, source="real", is_preview=False, status=Response.COMPLETED,
                                    pid=f"p{k}", qversion=1, answers={str(i): a for i, a in answers.items()}, layout={}))
        db.session.commit()


def _meta(app):
    with app.app_context():
        return export._spss_dictionary(db.session.get(Survey, 1), False, ("real",), None, True, None)


# --- N-2: open data, licence and repository fixed after the first real consent ------------------------------------

def test_open_data_fixed_after_the_first_real_consent(app, client, survey):
    fill_consent(client)
    add(client, "text_input")
    client.post("/surveys/1/consent-facts", data={"min_age": "18", "duration": "10"})  # open data off
    with app.app_context():  # the survey runs and one person agreed
        s = db.session.get(Survey, 1)
        s.status = "running"
        db.session.add(Response(survey_id=1, source="real", is_preview=False, status=Response.COMPLETED, pid="p1",
                                qversion=1, answers={str(s.consent_item.id): "1"}, layout={}))
        db.session.commit()
    client.post("/surveys/1/consent-facts", data={"min_age": "18", "duration": "10", "open_data": "1",
                                                  "repository": "Zenodo", "license": "CC0"})
    with app.app_context():
        item = db.session.get(Survey, 1).consent_item
        assert not item.config.get("open_data") and not item.config.get("repository") and not item.config.get("license")
        assert "veröffentlicht" not in dict(item.kind.export_columns(item))[f"{item.code}_data"]
        assert item.kind.open_data_fixed(item)
    html = client.get("/surveys/1").data.decode()
    assert 'name="open_data" value="1"  disabled' in html or 'name="open_data" value="1" disabled' in html


def test_open_data_may_change_before_a_real_consent(app, client, survey):
    fill_consent(client)
    client.post("/surveys/1/consent-facts", data={"min_age": "18", "duration": "10", "open_data": "1",
                                                  "repository": "Zenodo"})
    with app.app_context():
        item = db.session.get(Survey, 1).consent_item
        assert item.config["open_data"] is True and item.config["repository"] == "Zenodo"


# --- N-3: settings that say how stored answers are read stay after "Zur Bearbeitung öffnen" -------------------------

def test_side_by_side_dont_know_and_condition_stay(app, client, survey):
    item_id = setup_sbs(client)                    # column c2 "Wie zufrieden?" with "weiß nicht"
    answer = {"r1": {"c1": 1, "c2": "dk"}}
    _reopened_with_answers(app, {item_id: answer})
    form = editor_form(item_id)
    form.pop("col_dk_c2")
    html = client.post(f"/surveys/1/editor/items/{item_id}", data=form, follow_redirects=True).data.decode()
    assert "weiß nicht" in html and "bleiben, wie sie sind" in html
    client.post(f"/surveys/1/editor/items/{item_id}", data=editor_form(item_id, col_when_c2="c1:1"))
    with app.app_context():
        item = db.session.get(Item, item_id)
        col = item.kind.column(item, "c2")
        assert col.get("dont_know") is True and not col.get("when")
        vals = item.kind.export_values(item, {"r1": {"c1": 2, "c2": 4}})
        assert vals["mob_zuf_bus"] == 4                                     # not -77 "nicht gefragt"


def test_likert_dont_know_stays_but_may_be_switched_on(app, client, survey):
    add(client, "likert")
    data = MultiDict([("code", "L"), ("question", "Wie sehr?"), ("points", "5"), ("labeling", "full"), ("dont_know", "1"),
                      ("stmt_id", "s1"), ("stmt_code", "s1c"), ("stmt_text", "A"), ("stmt_id", "s2"), ("stmt_code", "s2c"),
                      ("stmt_text", "B")])
    client.post("/surveys/1/editor/items/2", data=data)
    _reopened_with_answers(app, {2: {"s1": "dk", "s2": 4}})
    data.pop("dont_know")
    client.post("/surveys/1/editor/items/2", data=data)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.config.get("dont_know") is True
        assert -98 in item.kind.missing_codes(item, "s1c")
        # offering "weiß nicht" from now on rereads nothing
        off = {**item.config, "dont_know": False}
        assert not answer_lock.refused_shape(db.session.get(Survey, 1), item, answer_lock.shape_of(item, "L", off),
                                             answer_lock.shape(item))


def test_image_mark_mode_stays(app, client, survey):
    setup_mark(client, app, mode="points")
    _reopened_with_answers(app, {2: [[0.2, 0.2], [0.6, 0.6]]})
    client.post("/surveys/1/editor/items/2", data=MultiDict({
        "code": "look", "question": "Was fällt Ihnen auf?", "answer_mode": "required", "mode": "rate", "alt": "Roboter",
        **AREAS}))
    with app.app_context():
        assert db.session.get(Item, 2).config["mode"] == "points"


def test_card_sort_mode_and_fewer_own_groups_stay_more_allowed(app, client, survey):
    setup_cards(client, mode="hybrid", required=False)
    _reopened_with_answers(app, {2: {"s": {"1": ["ki"], "101": ["robo", "app"]}, "own": {"101": "Maschinen"}}})
    with app.app_context():
        s, item = db.session.get(Survey, 1), db.session.get(Item, 2)
        now = answer_lock.shape(item)

        def refused(**change):
            return answer_lock.refused_shape(s, item, now, answer_lock.shape_of(item, "cs", {**item.config, **change}))
        assert refused(mode="closed") and refused(max_own=2) and refused(rank=True)
        assert not refused(max_own=5)


def test_q_sort_presort_and_grid_stay(app, client, survey):
    setup_q(client, presort=True, required=False)
    _reopened_with_answers(app, {2: {"q": {"s1": -2, "s2": 2, "s3": 0}}})
    with app.app_context():
        s, item = db.session.get(Survey, 1), db.session.get(Item, 2)
        now = answer_lock.shape(item)
        assert answer_lock.refused_shape(s, item, now, answer_lock.shape_of(item, "qs", {**item.config, "presort": False}))
        assert answer_lock.refused_shape(s, item, now, answer_lock.shape_of(item, "qs", {**item.config, "capacities": [1, 1, 1, 1, 1]}))


# --- N-4, N-6: value labels the SPSS/R/jamovi files read from the codebook ----------------------------------------

def test_q_sort_value_labels_name_both_ends(app, client, survey):
    setup_q(client, presort=False, required=False)
    _real(app, {2: {"q": {"s1": -2, "s2": 2, "s3": 0}}})
    _cols, _data, meta = _meta(app)
    assert dict(meta["qs_s1"]["pairs"]) == {"-2": "gar nicht", "2": "voll"}


def test_card_sort_value_label_per_own_group(app, client, survey):
    setup_cards(client, mode="hybrid", required=False)
    _real(app, {2: {"s": {"1": ["ki"], "101": ["robo"], "102": ["app"]}, "own": {"101": "Maschinen", "102": "Handy"}}})
    _cols, _data, meta = _meta(app)
    pairs = dict(meta["cs_ki"]["pairs"])
    assert pairs["101"] == "eigener Stapel 1" and pairs["102"] == "eigener Stapel 2" and pairs["103"] == "eigener Stapel 3"


# --- N-5: side-by-side, a multiple choice column without a tick in an answered row ---------------------------------

def test_side_by_side_multi_without_tick_is_0(app, client, survey):
    item_id = setup_sbs(client, col_kind_c5="multi", col_title_c5="Wofür?")
    a = parse_sbs(app, item_id, [("r1_c1", "1"), ("r1_c5", "1"), ("r2_c1", "2"), ("r3_c1", "2")])
    b = parse_sbs(app, item_id, [("r1_c1", "1"), ("r1_c2", "3"), ("r2_c1", "2"), ("r3_c1", "2")])
    with app.app_context():
        item = db.session.get(Item, item_id)
        va, vb = item.kind.export_values(item, a), item.kind.export_values(item, b)
        col = next(c for c in va if c.startswith("mob_anm_bus_"))
        assert (va[col], vb[col]) == (1, 0)
        _kind, _unit, labels = item.kind.result_column(item, col)
        f = results.frequencies([va[col], vb[col]], labels)
        assert f["n"] == 2
        empty = item.kind.export_values(item, {"r1": {"c1": 1}})                    # a row stored before: stays empty
        assert empty[col] is None


# --- N-7: location without coordinates comes from the stored answer (tests/items/test_geolocation.py) --------------------

# --- N-8: hot spot sent without a choice -------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["areas", "rate"])
def test_hot_spot_sent_without_a_choice_is_0(app, client, survey, mode):
    setup_hot(client, app, mode=mode)
    with app.app_context():
        item = db.session.get(Item, 2)
        item.answer_mode = "optional"
        answer = item.kind.parse_answer(item, MultiDict({}))
        assert answer == ({} if mode == "rate" else [])
        vals = item.kind.export_values(item, answer)
        assert vals["look_n"] == 0 and vals["look_logo"] == 0
        table = [item.kind.export_values(item, answer), item.kind.export_values(item, ["logo"] if mode == "areas" else {"logo": 1})]
        res = item.kind.results(item, table, "de")
        assert res["persons"] == 2


# --- N-9, N-10: "Liste einfügen" and codes with a leading zero ----------------------------------------------------

def test_paste_skips_the_header_keeps_places_of_the_same_name_and_says_what_changed(app, client, survey):
    from tests.analysis.test_choice_search import _save

    fill_consent(client)
    add(client, "single_choice")
    _save(client, ("presentation", "search"))      # options 1 = Bäcker/in, 99 = nicht in der Liste
    paste = "\n".join(["Code;Gemeinde", "07337047;Neustadt", "16070034;Neustadt", "99;Altdorf", "Neustadt"])
    html = client.post("/surveys/1/editor/items/2", data=MultiDict(
        [("code", "beruf"), ("question", "Ihr Beruf?"), ("answer_mode", "optional"), ("presentation", "search"),
         ("option_id", "a"), ("option_code", "1"), ("option_label", "Bäcker/in"),
         ("option_id", "c"), ("option_code", "99"), ("option_label", "nicht in der Liste, und zwar:"), ("option_text", "c"),
         ("options_paste", paste)]), follow_redirects=True).data.decode()
    with app.app_context():
        item = db.session.get(Item, 2)
        labels = item.text("options")
        got = [(o["code"], labels.get(o["id"])) for o in item.config["options"]]
        assert ("Code", "Gemeinde") not in got
        assert ("07337047", "Neustadt") in got and ("16070034", "Neustadt") in got
        altdorf = next(c for c, label in got if label == "Altdorf")
        assert altdorf not in ("99", "7337048")                             # 99 taken: numbered anew, and said so
        assert any("doppelte Beschriftung" in w for w in item.kind.check_warnings(item))
    assert "Kopfzeile" in html and f"99 → {altdorf}" in html and "Ausgelassen" in html


def test_code_with_leading_zero_stays_text_in_sav(app, client, survey):
    import pyreadstat

    from tests.analysis.test_choice_search import _save

    fill_consent(client)
    add(client, "single_choice")
    _save(client, ("presentation", "search"))
    _save(client, ("presentation", "search"), ("options_paste", "07337047;Neustadt a.d. Weinstr.\n16070034;Neustadt a.d. Orla"))
    _real(app, {2: "07337047"})
    cols, data, meta = _meta(app)
    assert meta["beruf"]["kind"] == "str" and ("07337047", "Neustadt a.d. Weinstr.") in meta["beruf"]["text_pairs"]
    raw = export.sav_file(cols, data, meta)
    path = os.path.join(tempfile.mkdtemp(), "x.sav")
    with open(path, "wb") as fh:
        fh.write(raw if isinstance(raw, bytes) else raw.getvalue())
    df, _m = pyreadstat.read_sav(path)
    assert df["beruf"].tolist() == ["07337047"]


# --- question on N: lists of places count like postal codes -------------------------------------------------------

def test_lists_of_places_are_open_columns(app, client, survey):
    from tests.analysis.test_choice_search import _save

    fill_consent(client)
    add(client, "single_choice")
    _save(client, ("presentation", "search"))
    with app.app_context():
        assert "beruf" not in export.open_columns(db.session.get(Survey, 1))
    _save(client, ("presentation", "search"), ("places_box", "1"), ("places", "1"))
    with app.app_context():
        assert db.session.get(Item, 2).config["places"] is True
        assert "beruf" in export.open_columns(db.session.get(Survey, 1))
    _save(client, ("presentation", "search"), ("places_box", "1"),
          ("options_paste", "05334002;Aachen\n05315000;Köln\n09162000;München"))
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.config["places"] is False and item.kind.place_columns(item) == ["beruf"]  # the keys say so


def test_drill_down_ticked_as_places(app, client, survey):
    from tests.items.test_drill_down import TABLE

    add(client, "drill_down")
    client.post("/surveys/1/editor/items/2", data=MultiDict([
        ("code", "ort"), ("question", "Wo wohnen Sie?"), ("answer_mode", "optional"), ("table", TABLE),
        ("places_box", "1"), ("places", "1")]))
    with app.app_context():
        open_ = export.open_columns(db.session.get(Survey, 1))
        assert {"ort_marke", "ort_modell", "ort_baujahr"} <= open_


# --- N-11: personal code, "weiß nicht" no longer the letter X -----------------------------------------------------

def test_personal_code_dont_know_is_no_letter(app, client, survey):
    from tests.accounts.test_personal_code import FULL, sent, setup_code

    setup_code(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        xenia = item.kind.parse_answer(item, sent(item, **{**FULL, "mother_first": "X"}))
        unknown = item.kind.parse_answer(item, sent(item, **{**FULL, "mother_first": None}))
        typed = item.kind.parse_answer(item, sent(item, **{**FULL, "mother_first": "?"}))
        assert xenia["mother_first"] == "X" and unknown["mother_first"] == DONT_KNOW and typed["mother_first"] == DONT_KNOW
        assert item.kind.export_values(item, xenia) != item.kind.export_values(item, unknown)
        assert item.kind.export_values(item, unknown)["pcode"] == "?07K1A"
        # an answer stored before (DONT_KNOW, not "X") reads the new way too
        assert item.kind.export_values(item, {**unknown, "birth_day": DONT_KNOW})["pcode"] == "???K1A"


# --- N-12: video played to the end before counts after an error on the page -----------------------------------------

def test_media_played_to_the_end_before_counts(app, client, survey):
    from tests.analysis.test_stimulus import _log, _media, _upload

    _media(app, client, advance="end")
    _upload(client, seconds="10")
    with app.app_context():
        item = db.session.get(Item, 2)
        full = item.kind.parse_answer(item, MultiDict({"i2": _log((100, "play", 0), (10100, "seg", 0, 10, 0), (10100, "end", 10))}))
        short = MultiDict({"i2": _log((100, "play", 10), (200, "pause", 10))})
        with pytest.raises(AnswerError):
            item.kind.parse_answer(item, short)
        assert item.kind.parse_answer(item, short, answers={2: full})["log"]


# --- L-14: the consent says that the personal code goes with the answers ------------------------------------------

def test_consent_names_the_personal_code(app, client, survey):
    from tests.accounts.test_personal_code import setup_code

    fill_consent(client)
    with app.app_context():
        item = db.session.get(Survey, 1).consent_item
        assert "persönlicher Code" not in item.kind.template_body(item, "de")
    setup_code(client)
    with app.app_context():
        item = db.session.get(Survey, 1).consent_item
        assert "Ihr persönlicher Code verbindet" in item.kind.template_body(item, "de")
        assert "Your personal code links" in item.kind.template_body(item, "en")
        assert item.kind.facts(item, "en")["personal_code"]


def test_paste_with_a_unicode_digit_as_code(app, client, survey):
    """Code review 2026-10-15, K4: "²" or "١" as the first cell counted as a number for str.isdigit, and int() gave a
    500. A code is ASCII, as in the editor (CODE_RE): such a cell stays part of the label, and the option is numbered."""
    fill_consent(client)
    add(client, "single_choice")
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.options_from_paste(item, "3;Drei\n²;Hoch zwei\n١;Eins (arabisch)", "de") == 3
        labels = item.text("options")
        got = {labels.get(o["id"]): o["code"] for o in item.config["options"]}
        assert got == {"Drei": "3", "² Hoch zwei": "4", "١ Eins (arabisch)": "5"}
        options = item.config["options"]                                                # such a code from before
        item.config = {**item.config, "options": [{**options[0], "code": "²"}] + options[1:]}
        assert item.kind.options_from_paste(item, "Vier", "de") == 1
