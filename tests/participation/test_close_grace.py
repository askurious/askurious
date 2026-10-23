"""Participations begun before a survey closed (field end, "Beenden", a pause) may be finished for CLOSE_GRACE_HOURS
after the closing; later, and at once in an archived survey, they show the page "Befragung beendet" and stay
"begonnen" (audit 2026-10-18, BIZ-04: before, they could be finished weeks after the data were exported)."""
from datetime import timedelta

from app.extensions import db
from app.models import Response, Survey, utcnow
from tests.conftest import CONSENT_OK
from tests.participation.test_participate import build, uid

PAGE_2 = {"nav": "next", "i2_s1": "3", "i2_s2": "3", "i3": "40"}


def _begun(app, client, n=2):
    build(client)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    key, people = uid(app), []
    for _ in range(n):
        p = app.test_client()
        p.get(f"/s/{key}")
        assert p.post(f"/s/{key}", data=CONSENT_OK).status_code == 302
        people.append(p)
    return key, people


def _finish(p, key):
    p.post(f"/s/{key}", data=PAGE_2)
    return p.post(f"/s/{key}", data={"nav": "next"}, follow_redirects=True)


def _statuses(app):
    with app.app_context():
        return sorted(db.session.scalars(db.select(Response.status)).all())


def _closed_ago(app, hours):
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.closed_at = utcnow() - timedelta(hours=hours)
        db.session.commit()


def test_begun_participations_finish_within_the_grace_only(app, client, survey):
    key, (early, late) = _begun(app, client)
    client.post("/surveys/1/close")
    _closed_ago(app, 23)
    _finish(early, key)  # within 24 hours of the closing: finished as before
    assert _statuses(app) == ["active", "completed"]
    _closed_ago(app, 25)
    page = late.post(f"/s/{key}", data=PAGE_2)
    assert page.status_code == 200 and "beendet" in page.data.decode()
    assert late.get(f"/s/{key}").status_code == 200
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(status="active"))
        assert r.position == 1 and not (r.answers or {}).get("3")  # nothing of the page was stored
    assert _statuses(app) == ["active", "completed"]
    app.config["CLOSE_GRACE_HOURS"] = -1  # no time limit, as before
    _finish(late, key)
    assert _statuses(app) == ["completed", "completed"]


def test_an_archived_survey_takes_nothing(app, client, survey):
    key, (p,) = _begun(app, client, n=1)
    client.post("/surveys/1/close")
    client.post("/surveys/1/archive", data={"archived": "1"})
    with app.app_context():
        assert db.session.get(Survey, 1).archived
    assert "beendet" in _finish(p, key).data.decode()
    assert _statuses(app) == ["active"]
