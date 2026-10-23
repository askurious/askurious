"""Audit Daten und Berechnungen 2026-10-12, Paket G (Papierfragebogen): doppelte und unvollständige Rangplätze als
offene Stelle (D-8, D-9), ein zweiter Bogen mit demselben Code bleibt mit Bild und gemessener Abweichung (D-11),
„ohne Prüfung“ legt Rang- und Zahlenfelder trotzdem vor (Entscheidung 10), Median der Bearbeitungszeit im Zeitverlauf
ohne Papier (S-7)."""
from datetime import timedelta

from app.extensions import db
from app.models import Item, PaperPage, PaperSheet, Response, Survey, utcnow
from tests.conftest import add, fill_consent, fill_questions
from tests.exports.test_paper import _all_pages, _field, _pdf, _run, _setup, _sheets, _unchecked, _upload, _yes


def _ranking_setup(app, client, review="unsure"):
    _unchecked(client)
    if review != "unsure":
        client.post("/surveys/1/paper/settings", data={"review": review, "keep": "30"})
    fill_consent(client)
    add(client, "ranking")
    with app.app_context():
        rk = db.session.get(Item, 2)
        rk.code = "rk"
        rk.config = {**rk.config, "randomize": False,
                     "options": [{"id": "a", "code": "1"}, {"id": "b", "code": "2"}, {"id": "c", "code": "3"}]}
        rk.texts = {"de": {"question": "Ordnen Sie", "options": {"a": "Auto", "b": "Bahn", "c": "Rad"}}}
        db.session.commit()
    fill_questions(app)
    client.post("/surveys/1/start", data={})
    with app.app_context():
        assert db.session.get(Survey, 1).status == "running"
    _run(client, 1)
    s = _sheets(app)[0]
    return s, [n for n, f in enumerate(s["plan"]["fields"]) if f.get("rank")]


def _state(app, sheet_id):
    with app.app_context():
        sheet = db.session.get(PaperSheet, sheet_id)
        return sheet.state, (db.session.get(Response, sheet.response_id) if sheet.response_id else None)


def test_tied_ranks_are_an_open_place(app, client, survey):
    s, ranks = _ranking_setup(app, client)
    assert _upload(client, _pdf(_all_pages(s, _yes(s["plan"]), writes=ranks))).status_code == 302
    data = {f"w_{n}": v for n, v in zip(ranks, ("1", "1", "2"))}
    html = client.post(f"/surveys/1/paper/sheets/{s['id']}", data={**data, "action": "take"},
                       follow_redirects=True).data.decode()
    assert "Rangplätze doppelt" in html
    assert _state(app, s["id"])[0] == "review"                     # not taken over with an invented order
    data = {f"w_{n}": v for n, v in zip(ranks, ("1", "3", "2"))}
    client.post(f"/surveys/1/paper/sheets/{s['id']}", data={**data, "action": "take"})
    state, r = _state(app, s["id"])
    assert state == "done" and r.answers["2"] == ["1", "3", "2"]


def test_partial_ranks_are_an_open_place(app, client, survey):
    s, ranks = _ranking_setup(app, client)
    assert _upload(client, _pdf(_all_pages(s, _yes(s["plan"]), writes=ranks[:2]))).status_code == 302
    data = {f"w_{ranks[0]}": "1", f"w_{ranks[1]}": "2", f"w_{ranks[2]}": "", "action": "take"}
    html = client.post(f"/surveys/1/paper/sheets/{s['id']}", data=data, follow_redirects=True).data.decode()
    assert "Rangplätze unvollständig" in html
    assert _state(app, s["id"])[0] == "review"                     # the ranks written are not lost unseen
    data = {f"w_{n}": "" for n in ranks}                            # cleared on purpose: taken over without an answer
    client.post(f"/surveys/1/paper/sheets/{s['id']}", data={**data, "action": "take"})
    state, r = _state(app, s["id"])
    assert state == "done" and (r.answers or {}).get("2") is None


def test_without_checking_ranks_are_still_put_before_a_person(app, client, survey):
    s, ranks = _ranking_setup(app, client, review="none")
    assert _upload(client, _pdf(_all_pages(s, _yes(s["plan"]), writes=ranks))).status_code == 302
    assert _state(app, s["id"])[0] == "review"
    html = client.get(f"/surveys/1/paper/sheets/{s['id']}").data.decode()
    assert "Rangplätze und Zahlen abtippen" in html
    data = {f"w_{n}": v for n, v in zip(ranks, ("2", "1", "3"))}
    client.post(f"/surveys/1/paper/sheets/{s['id']}", data={**data, "action": "take"})
    state, r = _state(app, s["id"])
    assert state == "done" and r.answers["2"] == ["2", "1", "3"]


def test_a_second_filled_copy_keeps_its_image_and_says_it_differs(app, client, survey):
    _setup(app, client)
    _run(client, 1)
    s = _sheets(app)[0]
    plan = s["plan"]
    first = _yes(plan) + [_field(plan, "i2", "1")]
    second = _yes(plan) + [_field(plan, "i2", "2"), _field(plan, "i3", "3")]
    assert _upload(client, _pdf(_all_pages(s, first))).status_code == 302
    assert _upload(client, _pdf(_all_pages(s, second))).status_code == 302
    with app.app_context():
        doubles = db.session.scalars(db.select(PaperPage).filter_by(state="double")).all()
        assert doubles and all(p.filename for p in doubles)
        assert any((p.reading or {}).get("differs") for p in doubles)
        assert db.session.scalar(db.select(db.func.count(Response.id))) == 1
    html = client.get("/surveys/1/field").data.decode()
    assert "anders ausgefüllt als der gelesene Bogen" in html


def test_timeline_median_leaves_out_paper(app, client, survey):
    from app import timeline

    with app.app_context():
        now = utcnow()
        for n, (channel, minutes) in enumerate([(None, 10), (None, 12), ("paper", 0), ("paper", 0), ("paper", 0)]):
            db.session.add(Response(survey_id=1, language="de", answers={}, pid=f"p-{n}", source="real",
                                    is_preview=False, status=Response.COMPLETED, position=0, channel=channel,
                                    started_at=now - timedelta(minutes=minutes), completed_at=now))
        db.session.commit()
        built = timeline.build(db.session.get(Survey, 1))
        assert built["completed"] == 5
        assert built["median_minutes"] == 11
