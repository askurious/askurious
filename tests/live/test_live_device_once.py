"""One participation per device and run of a live vote, also for two first answers at the same moment (audit
Datenspeicherung und Berechnungen 2026-10-21, F8: a second tab or a script with the signed device id made two
participations, a double vote)."""
from app.extensions import db
from app.live import audience
from app.models import Response
from tests.live.test_live import add, make, send, steer


def test_two_first_answers_of_one_device_at_once_make_one_participation(app, client, monkeypatch):
    code, sid, block = make(app, client)
    q1 = add(app, client, sid, block, "single_choice", question="A?", options=["x", "y"])
    steer(client, code, action="start")
    first = app.test_client()
    page = first.get(f"/live/{code}").data.decode()
    device = page.split('data-device="', 1)[1].split('"', 1)[0]
    lock, done = audience._lock_device, []

    def meanwhile(survey, st, dev):
        # the other tab's first answer stored its participation after this request looked and found nothing (in one
        # session here: SQLite lets no second writer in while this request holds its write lock)
        done.append(audience._new_response(survey, st, device=dev).id)
        return lock(survey, st, dev)

    monkeypatch.setattr(audience, "_lock_device", meanwhile)
    assert send(first, code, {f"i{q1}": "1", "device": device}).json["ok"]
    assert len(done) == 1
    with app.app_context():
        rows = list(db.session.scalars(db.select(Response).filter_by(survey_id=sid)))
        assert len(rows) == 1 and rows[0].answers == {str(q1): "1"}
