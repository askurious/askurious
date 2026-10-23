"""Dual response for the CBC (config "dual"): the choice among the options, then "would you really choose it?"."""
import csv
import io
from types import SimpleNamespace

from app.extensions import db
from app.models import Item, Response, Survey
from tests.conftest import CONSENT_OK
from tests.analysis import test_conjoint

E = "/surveys/1/editor"


def dual_form(**kw):
    form = test_conjoint.conjoint_form(**kw)
    form["none_mode"] = "dual"
    return form


def setup(client):
    test_conjoint.setup(client)
    client.post(E + "/items/2", data=dual_form())
    client.post(E + "/items/2/conjoint/design")


def start(app, client):
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        return db.session.get(Survey, 1).code


def test_editor_choice_of_none_mode(app, client, survey):
    setup(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.config["dual"] and not item.config["none"] and item.kind.dual(item)
    page = client.get(E + "/items/2").get_data(as_text=True)
    assert 'name="none_mode"' in page and '<option value="dual" selected' in page and 'name="dr_question"' in page
    form = test_conjoint.conjoint_form()
    form["none_mode"] = "option"
    client.post(E + "/items/2", data=form)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.config["none"] and not item.config["dual"] and not item.kind.dual(item)
    client.post(E + "/items/2", data=test_conjoint.conjoint_form(none=True))  # older forms: the tick "none"
    with app.app_context():
        assert db.session.get(Item, 2).config["none"]


def test_participation_exports_and_cho(app, client, survey):
    setup(client)
    uid = start(app, client)
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    html = p.get(f"/s/{uid}").data.decode()
    assert "Würden Sie sich in Wirklichkeit für die gewählte Option entscheiden?" in html
    assert 'name="i2_dt0" value="1"' in html and 'value="0"' in html and "Keine davon" not in html
    assert 'name="i2_t0" value="0"' not in html  # no "none" among the options
    for t, (choice, again) in enumerate([("1", "1"), ("2", "0"), ("1", "1"), ("2", "1")]):
        p.get(f"/s/{uid}")
        p.post(f"/s/{uid}", data={"nav": "next", f"i2_t{t}": choice, f"i2_dt{t}": again})
    assert "Vielen Dank" in p.get(f"/s/{uid}").data.decode()
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(is_preview=False))
        assert r.answers["2"] == {"t0": 1, "dt0": 1, "t1": 2, "dt1": 0, "t2": 1, "dt2": 1, "t3": 2, "dt3": 1}
    wide = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))[0]
    assert [wide[f"cbc_t{n}_dr"] for n in range(1, 5)] == ["1", "0", "1", "1"]
    codebook = client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    assert "cbc_t2_dr" in codebook and "1 = Ja; 0 = Nein" in codebook
    long = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/conjoint/2.csv").data.decode("utf-8-sig"))))
    assert len(long) == 4 * 2 and [r["buy"] for r in long if r["task"] == "2"] == ["0", "0"]
    page = client.get("/surveys/1/results").get_data(as_text=True)
    assert "Nachfrage (Dual Response) in 4 Aufgaben beantwortet" in page and "„würde nicht wählen“ in 1" in page


def test_follow_up_question_is_required_with_the_choice(app, client, survey):
    setup(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        item.answer_mode = "required"
        db.session.commit()
    uid = start(app, client)
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    p.get(f"/s/{uid}")
    html = p.post(f"/s/{uid}", data={"nav": "next", "i2_t0": "1"}, follow_redirects=True).data.decode()
    assert "Bitte beantworten Sie auch die Frage unter den Optionen." in html


def test_choice_sets_forced_choice_plus_follow_up(app, client, survey):
    from app import cbc_analysis

    setup(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        layout = {"cj": {"2": {"version": 0}}}
        r = SimpleNamespace(layout=layout, answers={"2": {"t0": 2, "dt0": 0, "t1": 1, "dt1": 1, "t2": 1, "dt2": None}})
        sets, coding, persons, _ = cbc_analysis.choice_sets(item, [r])
        # coding of the follow-up question (audit 2026-10-06, W-1): "no" = forced choice + "none" chosen among options and "none";
        # "yes" = one choice among options and "none" (no forced choice); unanswered = the forced choice only
        assert persons == 1 and coding.asc is not None and len(sets) == 2 + 1 + 1
        forced = [s for s in sets if len(s.X) == 2]
        follow = [s for s in sets if len(s.X) == 3]
        assert all(not s.X[:, coding.asc].any() for s in forced)
        assert all(list(s.X[:, coding.asc]) == [0, 0, 1] for s in follow)
        assert [s.chosen for s in forced] == [1, 0]   # task 0 (option 2, "no") and task 2 (unanswered)
        assert [s.chosen for s in follow] == [2, 0]   # "no" chooses "none", "yes" the option chosen
        assert (follow[0].X[:2] == forced[0].X).all()  # the same options as the forced choice of that task
        counts = cbc_analysis.counts(item, [r], "de")
        assert counts["dual"] and counts["asked"] == 2 and counts["declined"] == 1 and not counts["none_offered"]


def test_dummy_answers_have_the_follow_up_question(app, client, survey):
    import random

    setup(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        rng = random.Random(1)
        seen = set()
        # every task of the design: the design is drawn anew per run, and one task with a clearly best option can
        # get "yes" from (almost) everyone, so a single task made the test flaky
        for task in range(4):
            for respondent in range(100):
                fields = item.kind.dummy_fields(item, rng, {"task": task, "respondent": respondent,
                                                           "cj_state": {2: {"version": 0}}})
                assert fields[f"i2_t{task}"] in ("1", "2") and fields[f"i2_dt{task}"] in ("0", "1")
                seen.add(fields[f"i2_dt{task}"])
        assert seen == {"0", "1"}


def test_hb_input_carries_the_follow_up_sets(app, client, survey):
    from app import conjoint_hb, export

    setup(client)
    uid = start(app, client)
    for n in range(conjoint_hb.hb.MIN_PERSONS):
        p = app.test_client()
        p.get(f"/s/{uid}")
        p.post(f"/s/{uid}", data=CONSENT_OK)
        for t in range(4):
            p.get(f"/s/{uid}")
            p.post(f"/s/{uid}", data={"nav": "next", f"i2_t{t}": str(1 + (n + t) % 2), f"i2_dt{t}": str(n % 2)})
    with app.app_context():
        item = db.session.get(Item, 2)
        inp = conjoint_hb.model_input(item, export.rows(item.survey))
        assert inp and inp["coding"]["asc"]
        sizes = [len(X) for X, _ in inp["persons"][0]["sets"]]
        # person 0 said "no" four times: 4 forced choices between the two options + 4 choices of "none" among them
        assert sizes.count(2) == 4 and sizes.count(3) == 4
        sizes = [len(X) for X, _ in inp["persons"][1]["sets"]]
        assert sizes == [3] * 4  # person 1 said "yes": one choice among the options and "none" per task
