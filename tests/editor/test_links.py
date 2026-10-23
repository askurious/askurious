import re
from types import SimpleNamespace

from app import links, models
from app.extensions import db
from app.models import Response, Survey, SurveyLabel
from tests.conftest import CONSENT_OK
from tests.participation.test_participate import build

CONFUSABLE = set("0o1li")


def _code(app, sid=1):
    with app.app_context():
        return db.session.get(Survey, sid).code


def test_code_is_short_and_unambiguous(app, client, monkeypatch):
    codes = [models.random_code() for _ in range(300)]
    assert all(len(c) == 6 and not (set(c) & CONFUSABLE) and not c.isdigit() for c in codes)
    monkeypatch.setattr(models, "new_code", models.random_code)                     # real codes, not the test ones
    client.post("/surveys/new", data={"title": "Test", "languages": ["de"], "default_language": "de"})
    code = _code(app)
    assert re.fullmatch(r"[a-z2-9]{6}", code)
    assert f"/surveys/{code}/editor/" in client.get(f"/surveys/{code}").data.decode()  # links use the code
    assert client.get(f"/surveys/{code.upper()}/field").status_code == 200           # case does not matter
    assert client.get("/surveys/1/field").status_code == 404                          # no numeric ids
    with app.app_context():
        uid = db.session.get(Survey, 1).uid
    assert app.test_client().get(f"/s/{uid}").status_code == 404                     # nor the internal uid


def test_participant_link_by_code_and_label(app, client, survey):
    build(client)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        code = s.code
    for key in (code, code.upper()):
        assert "Einwilligung" in app.test_client().get(f"/s/{key}").data.decode()
    assert client.post("/surveys/1/label", data={"label": "Mensa-Studie"}).status_code == 302  # not confirmed
    with app.app_context():
        assert db.session.get(Survey, 1).label is None
    client.post("/surveys/1/label", data={"label": "Mensa-Studie", "confirm": "1"})
    p = app.test_client()
    assert "Einwilligung" in p.get("/s/mensa-studie").data.decode()
    r = p.post("/s/mensa-studie", data=CONSENT_OK)
    assert r.headers["Location"].endswith("/s/mensa-studie")
    assert "/s/mensa-studie" in client.get(f"/surveys/{code}").data.decode()
    # a label is given only once: a second one is refused, the first stays
    r = client.post("/surveys/1/label", data={"label": "mensa-2026", "confirm": "1"}, follow_redirects=True)
    assert "nur einmal vergeben" in r.data.decode()
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.label == "mensa-studie" and [l.label for l in s.labels] == ["mensa-studie"]
    assert "Label festlegen" not in client.get("/surveys/1/field").data.decode()          # no form any more
    assert "Einwilligung" in app.test_client().get("/s/mensa-studie").data.decode()
    assert "bereits vergeben" in client.get("/surveys/label-check?label=Mensa-Studie").json["error"]


def test_label_rules(app, client, survey):
    from flask import session
    from flask_login import login_user
    from app import twofactor
    from app.models import User

    researcher = SimpleNamespace(is_admin=False, id=None)
    with app.test_request_context():
        admin = db.session.get(User, 1)
        login_user(admin)
        session[twofactor.SIGNED_IN_VIA] = twofactor.VIA_ORCID
        s = db.session.get(Survey, 1)
        assert "mindestens 5" in links.label_error("hci", researcher)
        assert links.label_error("hci", admin) is None                    # short labels only for site admins
        assert "Kleinbuchstaben" in links.label_error("2cool", researcher)
        assert "Kleinbuchstaben" in links.label_error("mit leerzeichen", researcher)
        s.code = "abc2ef"
        assert "vergeben" in links.label_error("abc2ef", researcher)       # never a code of another survey
        assert links.set_label(s, "hci", admin) is None
        db.session.commit()
        db.session.delete(s)                                             # labels outlive their survey
        db.session.commit()
        assert db.session.get(SurveyLabel, "hci").survey_id is None
        assert "vergeben" in links.label_error("hci", admin)
    assert app.test_client().get("/s/hci").status_code == 404


def test_label_on_creation(app, client):
    r = client.post("/surveys/new", data={"title": "Mit Label", "languages": ["de"], "default_language": "de",
                                      "label": "Kurs-Umfrage"})                    # not confirmed: not created
    assert "nicht bestätigt" in r.data.decode()
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Survey.id))) == 0
    client.post("/surveys/new", data={"title": "Mit Label", "languages": ["de"], "default_language": "de",
                                      "label": "Kurs-Umfrage", "confirm": "1"})
    with app.app_context():
        assert db.session.scalar(db.select(Survey)).label == "kurs-umfrage"


def test_qr_codes_and_group_links(app, client, survey):
    code = _code(app)
    svg = client.get(f"/surveys/{code}/qr.svg")
    assert svg.mimetype == "image/svg+xml" and b"<svg" in svg.data
    png = client.get(f"/surveys/{code}/qr.png?name=gruppe&value=kurs-a&download=1")
    assert png.data[:8] == b"\x89PNG\r\n\x1a\n" and "qr-" in png.headers["Content-Disposition"]
    page = client.get(f"/surveys/{code}/qr?name=gruppe&value=kurs-a").data.decode()
    assert f"/s/{code}?gruppe=kurs-a" in page
    # a stored parameter with allowed values in the table of parameters (the group form is gone, Ralf 2026-10-06)
    client.post("/surveys/1/panel", data={"param_name": "gruppe", "param_mode": "store", "param_required": "0",
                                          "param_allowed": "kurs-a, kurs-b, kurs-c"})
    build(client)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    for group in ("kurs-a", "kurs-a", "kurs-b"):
        p = app.test_client()
        p.get(f"/s/{code}?gruppe={group}")
        p.post(f"/s/{code}", data=CONSENT_OK)
    with app.app_context():
        s = db.session.get(Survey, 1)
        from app import panel

        assert [(r["value"], r["started"]) for r in panel.group_counts(s)] == [("kurs-a", 2), ("kurs-b", 1), ("kurs-c", 0), (None, 0)]
        assert db.session.scalar(db.select(db.func.count(Response.id))) == 3
    html = client.get(f"/surveys/{code}/field").data.decode()
    assert f"/s/{code}?gruppe=kurs-b" in html and f"/surveys/{code}/qr?name=gruppe&amp;value=kurs-c" in html
    assert 'id="links"' in html


def test_participant_link_lives_on_the_field_page(client, survey):
    assert 'id="field-url"' in client.get("/surveys/1/field").data.decode()
    settings = client.get("/surveys/1").data.decode()
    assert 'id="participant-url"' not in settings and "data-label-form" not in settings
    r = client.post("/surveys/1/label", data={"label": "feld-test", "confirm": "1"})
    assert r.headers["Location"].endswith("/surveys/1/field#links")
