"""Published privacy texts against the code (audit 2026-10-10, Ralf: deviations are corrected in the texts):
backups, admin access, server logs, further services, map hosts, live nickname, Slack on the complaint page."""
import os

from app.extensions import db
from app.models import Survey
from tests.live.test_live import add as live_add
from tests.live.test_live import make as live_make
from tests.items.test_map_marker import setup as map_setup

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def test_terms_name_backups_admin_access_logs_and_services():
    from app import terms

    md, en = _read("licence.md"), _read("app", "templates", "terms_en.html")
    # backups: daily (backup.sh keeps 14) and before an update that migrates (at most 30 days)
    assert "Laufende Sicherungen (alle vier Stunden) werden nach 14 Tagen gelöscht" in md and "höchstens 30 Tagen" in md
    assert "regular backups (every four hours) are deleted after 14 days" in en
    # admin access: sign-in with ORCID, or password plus code (twofactor.required_at_signin; ADMIN_TOTP stays off)
    assert "mit Passwort und einem Code aus einer Authenticator-App" in md and "with a password and a code" in en
    # IP addresses: none in the database, but the error log of the web server may hold one
    assert "Die Plattform speichert keine IP-Adressen" not in md and "Die Datenbank der Plattform speichert keine" in md
    # how long the logs keep is on the privacy page; the terms say only that they are overwritten (terms 2026-10-17)
    assert "laufend überschrieben" in md and "overwritten continuously" in en
    for name in ("Transkription von Interviews", "Mailserver", "Slack", "DFN", "Rechenknecht", "Web Services",
                 "Eigener KI-Zugang"):
        assert name in md, name
    for name in ("Transcription of interviews", "mail server", "Slack", "DFN", "compute node", "Web services",
                 "Own AI access"):
        assert name in en, name
    # a clarification of the same day: no new version, the entry of 2026-10-10 names it (2026-10-10b: "KI-Nutzung")
    assert dict((v, t) for v, t, *_ in terms.CHANGES)["2026-10-10"].count("Server-Protokolle") == 1


def test_platform_privacy_page_names_configured_services(app, client):
    app.config.update(SLACK_WEBHOOK_URL="", COMPUTE_URL="", TSA_URL="")
    page = client.get("/imprint").data.decode()
    assert "Server-Protokolle und weitere Dienste" in page and "tile.openstreetmap.org" in page
    assert "nominatim.openstreetmap.org" in page and "Slack" not in page and "DFN" not in page
    assert "Eigener KI-Zugang" in page
    app.config.update(SLACK_WEBHOOK_URL="https://hooks.slack.test/x", COMPUTE_URL="http://compute:8090",
                      TSA_URL="http://zeitstempel.dfn.de", MAP_TILE_URL="https://tiles.example.org/{z}/{x}/{y}.png")
    page = client.get("/imprint").data.decode()
    assert "Slack" in page and "DFN-Vereins" in page and "Rechenknecht" in page and "tiles.example.org" in page
    assert "Sicherung vor jeder Aktualisierung, die die Datenbank umbaut" not in page  # BACKUP_DAYS 0: Plesk text
    assert "Vor einer Aktualisierung, die die Datenbank umbaut" in page
    client.post("/account/language", data={"lang": "en"})
    page = client.get("/imprint").data.decode()
    assert "Server logs and further services" in page and "tiles.example.org" in page and "Slack service" in page


def test_participant_privacy_names_other_map_servers(app, client, survey):
    map_setup(client)
    with app.app_context():
        uid = db.session.get(Survey, 1).public_key
    assert "Die Kartenbilder lädt Ihr Browser von OpenStreetMap" in client.get(f"/s/{uid}/privacy").data.decode()
    app.config["MAP_TILE_URL"] = "https://tiles.example.org/{z}/{x}/{y}.png"
    page = client.get(f"/s/{uid}/privacy").data.decode()
    assert "von diesen Servern: tiles.example.org, nominatim.openstreetmap.org" in page
    assert "lädt Ihr Browser von OpenStreetMap" not in page


def test_live_quiz_privacy_names_the_nickname(app, client):
    code, sid, block = live_make(app, client, "Quiz")
    q = live_add(app, client, sid, block, "single_choice", question="Hauptstadt?", options=["Berlin", "Bonn"])
    with app.app_context():
        uid = db.session.get(Survey, sid).public_key
    assert "Spitzname" not in client.get(f"/s/{uid}/privacy").data.decode()
    client.post(f"/presenter/{code}/control", data={"action": "quiz_set", "item": q, "on": "1", "correct": "1"})
    page = client.get(f"/s/{uid}/privacy").data.decode()
    assert "Für die Rangliste der Quizfragen wird mit Ihrer Teilnahme ein Spitzname gespeichert" in page


def test_complaint_page_names_slack_only_when_set(app, client, survey):
    with app.app_context():
        uid = db.session.get(Survey, 1).public_key
    app.config["SLACK_WEBHOOK_URL"] = ""
    assert "Slack" not in client.get(f"/s/{uid}/complaint").data.decode()
    app.config["SLACK_WEBHOOK_URL"] = "https://hooks.slack.test/x"
    page = client.get(f"/s/{uid}/complaint").data.decode()
    assert "über den Dienst Slack (Anbieter mit Sitz in den USA)" in page and "nicht Ihr Kontakt" in page
