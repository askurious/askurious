"""Coscine (app/coscine.py, experimental), Coscine-Ausbau 2026-10-20, package A: the token checked at Coscine on
"Konto" and once a day for the automatic upload (K7), and the storage of a resource before an upload (K4). Coscine's
API is replaced by FakeCoscine of test_coscine.py."""
import pytest

from app import coscine
from app.extensions import db
from app.models import Survey, UserLogin, utcnow
from tests.conftest import mail_on
from tests.exports.test_coscine import BASIS, PROFILES, connect, fake, jwt, on  # noqa: F401 - fake is a fixture

DCT = "http://purl.org/dc/terms/"


@pytest.fixture(autouse=True)
def _fresh_checks():
    coscine._checked.clear()
    yield
    coscine._checked.clear()


def _auto_on(app, user_id=1):
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.coscine = {"project": "p1", "resource": "r1", "auto": {"on": True, "user_id": user_id, "name": "Ralf Philipsen",
                                                                   "project": "p1", "resource": "r1"}}
        db.session.add(UserLogin(user_id=user_id, email="ralf@example.org"))
        db.session.commit()


def test_account_page_asks_coscine_about_the_token(app, client, fake):
    on(client)
    connect(client, fake)
    page = client.get("/account/connections").data.decode()
    assert "von Coscine bestätigt (Token „Askurious“)" in page and "lehnt dieses Token ab" not in page
    fake.token = "revoked"  # Coscine no longer takes it
    page = client.get("/account/connections").data.decode()
    assert "von Coscine bestätigt" in page  # kept a few minutes, no call to Coscine on every view
    coscine._checked.clear()
    page = client.get("/account/connections").data.decode()
    assert "Coscine lehnt dieses Token ab" in page and "von Coscine bestätigt" not in page


def test_the_end_coscine_lists_replaces_the_one_read_from_the_token(app, client, fake):
    on(client)
    connect(client, fake, jwt(days=200))
    with app.app_context():
        stored = db.session.scalar(db.select(coscine.CoscineConnection)).expires_at
    end = stored.replace(hour=(stored.hour + 3) % 24)
    fake.api_tokens = [{"id": "t0", "name": "Alt", "expiryDate": "2025-01-01T00:00:00Z"},
                       {"id": "t1", "name": "Für Askurious", "expiryDate": end.strftime("%Y-%m-%dT%H:%M:%SZ")}]
    coscine._checked.clear()  # the page after connecting asked already
    assert "Token „Für Askurious“" in client.get("/account/connections").data.decode()
    with app.app_context():
        assert db.session.scalar(db.select(coscine.CoscineConnection)).expires_at == end


def test_daily_check_warns_in_the_block_and_mails_once(app, client, survey, fake):
    on(client)
    connect(client, fake, jwt(days=10))
    _auto_on(app)
    sent = mail_on(app)
    with app.app_context():
        assert coscine.check_tokens(utcnow()) == 1
        assert coscine.check_tokens(utcnow()) is None  # once a day
        assert coscine._check_tokens() == 1  # the next day: the same finding, no second mail
    assert len(sent) == 1 and "läuft am" in sent[0]["Subject"] and sent[0]["To"] == "ralf@example.org"
    page = client.get("/surveys/1/data").data.decode()
    assert "Das Coscine-Token von Ralf Philipsen läuft am" in page

    fake.token = "revoked"
    with app.app_context():
        coscine._check_tokens()
    assert len(sent) == 2 and "lehnt Ihr Token ab" in sent[1]["Subject"]
    assert "Coscine lehnt das Token von Ralf Philipsen ab" in client.get("/surveys/1/data").data.decode()

    connect(client, fake, jwt(days=300))  # a new token: the warning goes at once
    page = client.get("/surveys/1/data").data.decode()
    assert "lehnt das Token von" not in page and "Das Coscine-Token von" not in page
    with app.app_context():
        coscine._check_tokens()
    assert len(sent) == 2


def test_daily_check_without_mail_still_warns(app, client, survey, fake):
    on(client)
    connect(client, fake, jwt(days=5))
    _auto_on(app)
    with app.app_context():
        coscine._check_tokens()
        token = db.session.get(Survey, 1).coscine["auto"]["token"]
    assert token["problem"] == "expiring" and token["mailed"] == ""


def _basis_form(target):
    fields = {f["name"]: f["key"] for f in coscine.Profile(BASIS, "Basis", PROFILES[BASIS]).fields}
    return {"ziel": target, "paket": "1", fields["Titel"]: "Test", fields["Ersteller"]: "Philipp",
            fields["Erstellungsdatum"]: "2026-10-20"}


def test_storage_shown_and_an_upload_that_does_not_fit_refused(app, client, survey, fake):
    from tests.conftest import add

    on(client)
    add(client, "likert")
    connect(client, fake)
    fake.quotas = {"r2": (950, 1000)}
    page = client.get("/coscine/survey/1?ziel=p1:r2").data.decode()
    assert "Speicher: 950 Bytes von 1,0 kB belegt (95,0 %), 50 Bytes frei." in page and "fast voll" in page
    assert "Speicher:" not in client.get("/coscine/survey/1?ziel=p1:r1").data.decode()  # a web resource: no quota
    r = client.post("/coscine/survey/1", data=_basis_form("p1:r2"))
    assert r.status_code == 502 and "passt nicht in die Ressource" in r.data.decode() and not fake.files

    fake.quotas = {"r2": (0, 10 ** 9)}
    r = client.post("/coscine/survey/1", data=_basis_form("p1:r2"), follow_redirects=True)
    assert "Dateien in Coscine abgelegt" in r.data.decode() and fake.files
    with app.app_context():
        state = db.session.get(Survey, 1).coscine
    assert state["space"]["resource"] == "Rohdaten S3" and state["space"]["percent"] == 0.0


def test_automatic_upload_refused_when_full_and_warned_from_90_percent(app, client, survey, fake):
    on(client)
    connect(client, fake)
    fake.quotas = {"r2": (990, 1000)}
    values = {BASIS: {DCT + "title": "Test", DCT + "creator": "Philipp", DCT + "created": "2026-10-20"}}
    job = {"project": "p1", "resource": "r2", "values": values, "uid": "u1", "person": "Ralf",
           "parts": [("data.csv", b"x" * 200, "data.csv")], "public_image_rights": True}
    with app.test_request_context():
        client_ = coscine.Client(fake.token)
        outcome = coscine._auto_send(client_, job)
        assert outcome[0] == "error" and "passt nicht" in outcome[1] and not fake.files
        fake.quotas = {"r2": (800, 1000)}
        outcome = coscine._auto_send(client_, job)
        assert outcome[0] == "ok" and outcome[1]["space"] == 100.0
    _auto_on(app)
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.coscine = {**s.coscine, "space": {"percent": outcome[1]["space"], "resource": "Rohdaten S3"}}
        db.session.commit()
    assert "Die Ressource „Rohdaten S3“ ist zu 100,0 % belegt" in client.get("/surveys/1/data").data.decode()
