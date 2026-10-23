"""Audit of data and calculations 2026-10-10, packages C and D: categorising open answers and the AI coding help
(B-1 to B-15 of kodieren-ergebnisse, L-2, L-9 and L-13 of lebenszyklus). Each test failed before the fix."""
import csv
import io
import json

import pytest

from app import ai, coding, coding_check
from app.extensions import db
from app.models import Response, Survey
from tests.conftest import add, experiments_on, own_ai
from tests.interview.test_coding import _add, _assign, _codings, _survey
from tests.interview.test_coding_stage2 import _second_person


def _rows(client):
    return list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))


def _respond(app, answers_list, source="real", prefix=None):
    with app.app_context():
        for n, answers in enumerate(answers_list):
            db.session.add(Response(survey_id=1, source=source, is_preview=False, status=Response.COMPLETED,
                                    pid=f"{prefix or source}{n}", qversion=1, answers=answers, layout={}))
        db.session.commit()


def _second_coding(app, client, key, codes):
    """Starts a second coding with the second person, who codes `codes` ({norm: category}) and finishes."""
    other, uid = _second_person(app)
    client.post(f"/surveys/1/categorise/{key}/second/start", data={"coder": str(uid)})
    for norm, cat in codes.items():
        other.post(f"/surveys/1/categorise/{key}/assign", data={"norm": norm, "slot": "second", "cat": str(cat)})
    other.post(f"/surveys/1/categorise/{key}/second/finish")
    return other


def _methods_de(app):
    with app.app_context():
        return coding.methods_sentences(db.session.get(Survey, 1))[0]["text_de"]


# --- B-1: the AI help ----------------------------------------------------------------------------------------------

def test_b1_ai_help_needs_the_tick_sends_only_real_answers_and_never_stores(app, client, survey, monkeypatch):
    key = _survey(app, client)
    _respond(app, [{"2": "Dummy-Antwort"}], source="dummy")
    app.config["OPENAI_API_KEY"] = "test"
    own_ai(app)  # the AI coding help runs only over an own access (2026-10-21)
    calls = []

    def fake(instructions, prompt, purpose="text", item_type=None, schema=None, store=False):
        calls.append(json.loads(prompt))
        return json.dumps({"categories": [{"name": "Preis", "definition": "zu teuer", "example": "teuer"}]})

    monkeypatch.setattr(ai, "generate_text", fake)
    page = client.get(f"/surveys/1/categorise/{key}").data.decode()
    assert "Teilnehmende wurden über die Übermittlung informiert" in page and "Kategorien vorschlagen lassen" not in page
    client.post(f"/surveys/1/categorise/{key}/ai/scheme")
    assert not calls
    guest = app.test_client()
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.status = Survey.RUNNING
        db.session.commit()
        uid = s.public_key
    assert "OpenAI" not in guest.get(f"/s/{uid}/privacy").data.decode()
    client.post(f"/surveys/1/categorise/{key}/ai/informed", data={"informed": "1"})
    page = guest.get(f"/s/{uid}/privacy").data.decode()  # the participants' privacy page names the own provider now
    assert "llm.example.org" in page and "OpenAI" not in page
    client.post(f"/surveys/1/categorise/{key}/ai/scheme")
    assert calls and "Dummy-Antwort" not in calls[0]["answers"] and "Zu teuer" in calls[0]["answers"]
    assert "one of the given answers, verbatim" not in coding.coding_ai.SCHEME_INSTRUCTIONS and "paraphrased" in coding.coding_ai.SCHEME_INSTRUCTIONS


def test_b1_text_model_is_called_with_store_false(app, monkeypatch):
    seen = {}

    class Client:
        class responses:  # noqa: N801
            @staticmethod
            def create(**kw):
                seen.update(kw)
                return type("R", (), {"output_text": "ok", "usage": None})()

    with app.test_request_context():
        monkeypatch.setattr(ai, "_client", lambda: Client)
        monkeypatch.setattr(ai, "_charge", lambda kind, **k: None)
        assert ai.generate_text("i", "p") == "ok"
    assert seen["store"] is False


# --- B-2, B-3: "nur eine Kategorie" -----------------------------------------------------------------------------

def test_b2_association_with_one_category_per_word_keeps_every_category(app, client, survey):
    experiments_on(client, "coding")
    add(client, "association")
    client.post("/surveys/1/editor/items/2", data={"code": "assoc", "question": "Roboter?", "slots": "3"})
    _respond(app, [{"2": [{"t": "Arbeit", "v": 1}, {"t": "Angst", "v": -2}]}])
    key = "2_assoc"
    _add(client, "Wirtschaft", key=key)
    _add(client, "Gefuehl", key=key)
    client.post(f"/surveys/1/categorise/{key}/scheme", data={"single": "1"})
    _assign(client, "arbeit", 1, key=key)
    _assign(client, "angst", 2, key=key)
    row = _rows(client)[0]
    assert (row["assoc_assoc_k1"], row["assoc_assoc_k2"]) == ("1", "1")
    assert (row["assoc_1_kat"], row["assoc_2_kat"], row["assoc_3_kat"]) == ("1", "2", "")
    with app.app_context():
        from app import results

        entry = next(e for e in results.compute(db.session.get(Survey, 1))["items"] if e["item"].id == 2)
    counts = {r["label"]: r["count"] for r in entry["codings"][0]["rows"]}
    assert counts["Gefuehl"] == 1 and counts["Wirtschaft"] == 1
    with app.app_context():
        assert "je Begriff eine Kategorie" in coding.guide_md(db.session.get(Survey, 1))


def test_b3_switching_to_one_category_looks_at_every_unit_and_slot(app, client, survey):
    from app import loops
    from app.models import Block
    from tests.conftest import fill_table
    from tests.editor.test_loops import loop_url

    experiments_on(client, "coding")
    add(client, "text_input")
    client.post("/surveys/1/editor/items/2", data={"code": "why", "question": "Warum {{robot}}?"})
    fill_table(client, loop_url(client), robot=["Pepper", "Nao"])
    with app.app_context():
        pepper, _ = [r["id"] for r in loops.config(db.session.get(Block, 2))["rows"]]
    _respond(app, [{f"2#{pepper}": "Zu teuer und haesslich"}])
    _add(client, "Preis")
    _add(client, "Aussehen")
    _assign(client, "zu teuer und haesslich", 1, 2)
    r = client.post("/surveys/1/categorise/2/scheme", data={"single": "1"}, follow_redirects=True)
    assert "1 Antworten haben schon mehrere Kategorien" in r.data.decode()
    with app.app_context():
        assert db.session.get(Survey, 1).codings["2"]["single"] is False


# --- B-4: blind everywhere -------------------------------------------------------------------------------------

def test_b4_second_coder_sees_no_categories_in_export_guide_and_results(app, client, survey):
    key = _survey(app, client)
    _add(client, "Preis")
    _assign(client, "zu teuer", 1)
    other, uid = _second_person(app)
    client.post(f"/surveys/1/categorise/{key}/second/start", data={"coder": str(uid)})
    rows = list(csv.DictReader(io.StringIO(other.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))
    assert rows and not any(c.startswith("why_k") for c in rows[0])
    assert "Preis" not in other.get("/surveys/1/export/kodierleitfaden.md").data.decode()
    assert "why_k1" not in other.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    # the first coder still sees them, also after the second coder computed the results
    other.get("/surveys/1/results")
    assert any(c.startswith("why_k") for c in _rows(client)[0])
    assert "kategorisierte Antworten" in client.get("/surveys/1/results").data.decode()
    assert "kategorisierte Antworten" not in other.get("/surveys/1/results").data.decode()


# --- B-5, B-6, B-9, B-12, B-14, B-15: the second coding and the methods section ------------------------------------

def test_b5_b6_b12_b15_second_coding_only_real_answers_and_honest_sentences(app, client, survey):
    key = _survey(app, client)
    _respond(app, [{"2": f"Dummytext {i}"} for i in range(6)], source="dummy")
    _add(client, "Preis")
    for norm, cat in (("zu teuer", 1), ("lieferung dauert", 98), ("toll", 99)):
        _assign(client, norm, cat)
    for i in range(6):
        _assign(client, f"dummytext {i}", 98)
    other, uid = _second_person(app)
    client.post(f"/surveys/1/categorise/{key}/second/start", data={"coder": str(uid)})
    page = other.get(f"/surveys/1/categorise/{key}/second").data.decode()
    assert "Dummytext" not in page  # the sample: real participations only
    for norm, cat in (("zu teuer", 1), ("lieferung dauert", 1), ("toll", 99)):
        other.post(f"/surveys/1/categorise/{key}/assign", data={"norm": norm, "slot": "second", "cat": str(cat)})
    other.post(f"/surveys/1/categorise/{key}/second/finish")
    de = _methods_de(app)
    assert "Zufallsstichprobe von 3 verschiedenen Antworttexten" in de
    assert "einer inhaltlichen Kategorie" in de and "je Antwort mehrere Kategorien möglich" in de  # B-15
    assert "gepoolt über die Ja/Nein-Entscheidungen je Kategorie" in de and "(identische Kategorien)" in de  # B-12
    assert "geklärt" not in de  # B-6: nothing was clarified
    with app.app_context():
        guide = coding.guide_md(db.session.get(Survey, 1))
    assert "5 Antworten, davon 5 kategorisiert" in guide  # the 6 dummy texts are not counted
    _assign(client, "lieferung dauert", 1)  # the disagreement clarified: the first coding changed
    assert "Die Abweichungen wurden anschließend geklärt." in _methods_de(app)


def test_b9_methods_round_half_up():
    assert coding._dec(0.125, "de") == "0,13" and coding._dec(0.625, "en") == "0.63"


def test_b14_l13_sample_keeps_the_pid_and_the_text_when_a_participation_goes(app, client, survey):
    key = _survey(app, client)  # "Zu teuer" given by p0, p1 and p5
    _add(client, "Preis")
    _assign(client, "zu teuer", 1)
    other = _second_coding(app, client, key, {"zu teuer": 1, "lieferung dauert": 98, "toll": 1})
    with app.app_context():
        ids = db.session.get(Survey, 1).codings[key]["second"]["ids"]
        assert all(isinstance(ref, str) for group in ids for ref, _ in group)  # pids, not row numbers (L-13)
        first = db.session.scalar(db.select(Response).filter_by(pid="p0"))
        db.session.delete(first)
        db.session.commit()
    with app.app_context():
        s = db.session.get(Survey, 1)
        item, column, _ = coding.find(s, key)
        texts = [u["norm"] for u in coding_check.units_of(s, item, column, key)]
        assert "zu teuer" in texts  # p1 still gives the text (B-14)
        rep = coding_check.report(s, item, column, key)
        assert rep["n"] == 1 and rep["skipped"] == 2  # still compared, through p1
    assert other


def test_b14_skipped_units_are_named(app, client, survey):
    key = _survey(app, client)
    _add(client, "Preis")
    _assign(client, "zu teuer", 1)
    _assign(client, "lieferung dauert", 1)
    _second_coding(app, client, key, {"zu teuer": 1, "lieferung dauert": 98, "toll": 1})
    assert "1 weitere ohne Erstkodierung wurden nicht verglichen" in _methods_de(app)


# --- B-7: the dashboard link after categorising -------------------------------------------------------------------

def test_b7_dashboard_link_follows_the_categories(app, client, survey):
    from datetime import timedelta

    from werkzeug.datastructures import MultiDict

    from app.models import BoardShare, utcnow

    app.config["BOARD_CHECK_SECONDS"] = 0
    app.config["BOARD_PUBLIC_SECONDS"] = 0
    key = _survey(app, client)
    _respond(app, [{"2": "Zu teuer"}] * 10, prefix="x")
    _add(client, "Preis")
    _add(client, "Lieferung")
    _assign(client, "zu teuer", 1)
    tile = "item:2@k"
    client.post("/surveys/1/dashboards/new", data={"template": "empty"})
    client.post("/surveys/1/dashboards/1", data=MultiDict([("name", "A"), ("pick", tile), (f"view-{tile}", "bars"),
                                                           (f"n-{tile}", "1"), (f"pct-{tile}", "1")]))
    client.post("/surveys/1/dashboards/1/link", data={"valid": "30"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.status = Survey.CLOSED
        for r in db.session.scalars(db.select(Response)):
            r.completed_at = utcnow() - timedelta(hours=1)
        db.session.commit()
        token = db.session.scalars(db.select(BoardShare)).first().token
    public = app.test_client()
    before = public.get(f"/d/{token}").data.decode()
    _assign(client, "zu teuer", 2)
    assert public.get(f"/d/{token}").data.decode() != before


# --- B-10: weighted order --------------------------------------------------------------------------------------

def test_b10_weighted_categories_sorted_by_the_weighted_share():
    from types import SimpleNamespace

    item = SimpleNamespace(id=3, code="why", type="text_input")
    found = {"single": True, "cats": [{"id": 1, "name": "Preis"}, {"id": 2, "name": "Lieferung"},
                                      {"id": 98, "name": "Sonstiges"}, {"id": 99, "name": "nicht verwertbar"}]}
    table = [{"why": "x", "why_kat": 1}] * 6 + [{"why": "y", "why_kat": 2}] * 4
    out = coding.results(item, [("why", "Warum?", "3", found)], table, [0.5] * 6 + [2.0] * 4)[0]
    assert [r["label"] for r in out["rows"]][:2] == ["Lieferung", "Preis"]


# --- B-13: merging after the end -----------------------------------------------------------------------------

def test_b13_merging_after_the_second_coding_says_alpha_is_new(app, client, survey):
    key = _survey(app, client)
    _add(client, "Preis")
    _add(client, "Kosten")
    for norm in ("zu teuer", "lieferung dauert", "toll"):
        _assign(client, norm, 1)
    _second_coding(app, client, key, {"zu teuer": 2, "lieferung dauert": 1, "toll": 1})
    assert "neu berechnet" not in _methods_de(app)
    client.post(f"/surveys/1/categorise/{key}/merge", data={"source": "2", "target": "1"})
    assert "α wurde nach einer späteren Änderung des Kategoriensystems neu berechnet" in _methods_de(app)
    assert "neu berechnet" in client.get(f"/surveys/1/categorise/{key}").data.decode()


# --- L-2: deletion ---------------------------------------------------------------------------------------------

def _anchored(app, client):
    key = _survey(app, client)
    _add(client, "Lieferzeit")
    client.post(f"/surveys/1/categorise/{key}/scheme",
                data={"name_1": "Lieferzeit", "definition_1": "Lieferung", "example_1": "Lieferung dauert"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        coding._store(s, key, {**coding.scheme(s, key), "proposals": [
            {"name": "Begeisterung", "definition": "lobt", "example": "Toll"}]})
        db.session.commit()
    return key


def test_l2_erasure_empties_quoting_examples_and_drops_proposals(app, client, survey):
    from app import erasure

    key = _anchored(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert erasure.erase(s, [db.session.scalar(db.select(Response).filter_by(pid="p0"))], "self") == 1
        assert "Lieferung dauert" in coding.guide_md(s)  # nobody deleted said that
        assert "proposals" not in s.codings[key]
        assert erasure.erase(s, [db.session.scalar(db.select(Response).filter_by(pid="p2"))], "self") == 1
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert "Lieferung dauert" not in coding.guide_md(s) and "Lieferung dauert" not in json.dumps(s.codings)


def test_l2_purge_empties_the_examples(app, client, survey):
    from app import retention

    _anchored(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        retention.purge(s)
        db.session.commit()
    with app.app_context():
        s = db.session.get(Survey, 1)
        text = json.dumps(s.codings, ensure_ascii=False)
        assert "Lieferung dauert" not in text and "Toll" not in text


# --- L-9: two people at work --------------------------------------------------------------------------------------

def test_l9_a_second_save_on_a_stale_scheme_is_refused(app, client, survey):
    key = _survey(app, client)
    _add(client, "Preis")
    with app.app_context():
        a = db.session.get(Survey, 1)
        before = coding.scheme(a, key)
        with app.app_context():
            b = db.session.get(Survey, 1)
            assert coding.add_category(b, key, "Lieferzeit") is None
            db.session.commit()
        with pytest.raises(coding.Conflict):
            coding._store(a, key, {**before, "single": True})
        db.session.rollback()
    with app.app_context():
        names = [c["name"] for c in coding.scheme(db.session.get(Survey, 1), key)["cats"]]
    assert "Lieferzeit" in names


def test_l9_a_form_from_before_is_refused_with_a_message(app, client, survey):
    key = _survey(app, client)
    _add(client, "Preis")
    with app.app_context():
        old = coding.version(db.session.get(Survey, 1).codings)
    _add(client, "Lieferung")
    r = client.post(f"/surveys/1/categorise/{key}/add", data={"name": "Rabatt", "base": str(old)}, follow_redirects=True)
    assert "jemand anderes die Kategorien geändert" in r.data.decode()
    with app.app_context():
        assert "Rabatt" not in [c["name"] for c in db.session.get(Survey, 1).codings[key]["cats"]]
    autosave = client.post(f"/surveys/1/categorise/{key}/scheme", data={"autosave": "1", "base": str(old), "name_1": "X"})
    assert autosave.get_json()["status"] == "conflict"
    assert _codings(app)  # nothing else broke
