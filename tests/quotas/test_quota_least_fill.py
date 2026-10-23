"""Least fill: when several Teilquoten fit a person, the least-filled one gets the place (app/quotas.py)."""
from werkzeug.datastructures import MultiDict

from app import quotas
from app.extensions import db
from app.models import Quota, QuotaCell, Response, Survey
from tests.conftest import add
from tests.quotas.test_quotas import E, Q, cells_form, counters, finish, questionnaire, set_flow, start


def media_survey(app, client, pick="least", targets=(4, 2), **settings):
    """gender (2), age (3), media (6, multiple choice: Zeitung 1, Radio 2); one Teilquote per medium."""
    questionnaire(client)
    add(client, "multiple_choice", block=2)
    client.post(E + "/items/6", data=MultiDict([("code", "media"), ("question", "Medien"),
                                                ("option_id", "a"), ("option_code", "1"), ("option_label", "Zeitung"),
                                                ("option_id", "b"), ("option_code", "2"), ("option_label", "Radio")]))
    client.post(Q + "/add")
    form = cells_form([("Zeitung", "z", targets[0], [(6, "in", "1")]), ("Radio", "r", targets[1], [(6, "in", "2")])],
                      pick=pick, **settings)
    assert "gespeichert" in client.post(Q + "/1", data=form, follow_redirects=True).data.decode()
    set_flow(app)
    return start(app, client)


def both(app, uid, media=("1", "2")):
    p = app.test_client()
    from tests.conftest import CONSENT_OK

    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    p.post(f"/s/{uid}", data=MultiDict([("nav", "next"), ("i2", "1"), ("i3", "30")] + [("i6", m) for m in media]))
    return p


def cells_of(app):
    with app.app_context():
        return [db.session.get(QuotaCell, r.quota["1"]["cell"]).code for r in db.session.scalars(db.select(Response).order_by(Response.id))]


def test_first_in_order_is_the_default(app, client, survey):
    uid = media_survey(app, client, pick="first")
    both(app, uid), both(app, uid), both(app, uid)
    assert cells_of(app) == ["z", "z", "z"]
    with app.app_context():
        assert db.session.get(Quota, 1).pick == "first"


def test_least_filled_cell_gets_the_place(app, client, survey):
    uid = media_survey(app, client)                                                         # targets: Zeitung 4, Radio 2
    people = [both(app, uid) for _ in range(6)]
    # shares after each person: 0 vs 0 -> z; 1/4 vs 0 -> r; 1/4 vs 1/2 -> z; 2/4 vs 1/2 -> z (order); 3/4 vs 1/2 -> r; then z
    assert cells_of(app) == ["z", "r", "z", "z", "r", "z"]
    assert counters(app, "z")["running"] == 4 and counters(app, "r")["running"] == 2
    full = both(app, uid)                                                                    # both cells are full now
    assert "bereits genügend" in full.get(f"/s/{uid}").data.decode()
    assert counters(app, "z")["refused"] + counters(app, "r")["refused"] == 1
    only_radio = both(app, uid, media=("2",))
    assert "bereits genügend" in only_radio.get(f"/s/{uid}").data.decode()                  # one fitting cell, full
    for p in people:
        finish(uid, p)
    assert (counters(app, "z")["done"], counters(app, "r")["done"]) == (4, 2)
    with app.app_context():
        assert not quotas.recount(db.session.get(Survey, 1))


def test_when_the_least_filled_has_no_room_the_next_is_tried(app, client, survey):
    uid = media_survey(app, client, targets=(4, 2))
    with app.app_context():
        # as another server process saw it: Radio looks empty here but is full in the database
        radio = db.session.scalar(db.select(QuotaCell).filter_by(code="r"))
        quota = db.session.get(Quota, 1)
        from sqlalchemy.orm import Session

        with Session(db.engine) as other:
            other.execute(db.update(QuotaCell).where(QuotaCell.id == radio.id).values(running=2))
            other.commit()
        assert [c.code for c in quotas.candidates(quota, {6: ["1", "2"]})] == ["z", "r"]     # equal shares: the order
        radio.running = 0                                                                     # the stale view of this process
        db.session.flush()
        db.session.execute(db.update(QuotaCell).where(QuotaCell.id == radio.id).values(running=2))
        db.session.commit()
    p = both(app, uid, media=("1", "2"))
    assert "Hauptteil" in p.get(f"/s/{uid}").data.decode()
    assert cells_of(app) == ["z"] and counters(app, "r") == {"running": 2, "done": 0, "refused": 0, "over": 0}


def test_closed_cells_and_the_door(app, client, survey):
    uid = media_survey(app, client, targets=(1, 1))
    with app.app_context():
        db.session.scalar(db.select(QuotaCell).filter_by(code="z")).closed = True
        db.session.commit()
    assert cells_of(app) == [] and "Hauptteil" in both(app, uid).get(f"/s/{uid}").data.decode()
    assert cells_of(app) == ["r"]                                                            # the closed one is passed over
    with app.app_context():
        s = db.session.get(Survey, 1)
        quota = s.quotas[0]
        assert quotas.fill(quota, quota.cells[1]) == 1.0 and quotas.fill(quota, QuotaCell(target=0, done=0, running=0)) == float("inf")
        quota.count_at = "check"
        assert quotas.fill(quota, quota.cells[1]) == 0.0                                     # reserved places do not count there


def test_dummy_data_and_the_page(app, client, survey):
    media_survey(app, client, targets=(3, 3))
    client.post("/surveys/1/dummy", data={"n": "30"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        tally = quotas.dummy_tally(s)
        assert max(tally.values()) <= 3 and sum(tally.values()) <= 6
    html = client.get(Q + "/").data.decode()
    assert "Passen mehrere Teilquoten" in html
    book = client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    assert "am wenigsten gefüllte passende Teilquote" in book
    client.post("/surveys/1/duplicate", data={"title": "Zweite Welle"})
    with app.app_context():
        assert db.session.scalar(db.select(Survey).filter_by(title="Zweite Welle")).quotas[0].pick == "least"
