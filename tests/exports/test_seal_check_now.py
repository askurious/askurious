"""„Jetzt prüfen“ of the seal chain (tab "Export", surveys.seal_check), pre-test 2026-10-20, finding 30: a new survey
starts with the chain on (SEAL_DEFAULT) but without an entry; the check answered 404 and the page showed only "Die
Prüfung ist fehlgeschlagen". Now it says there is nothing to check, checks an unstamped chain, and names the
timestamp service when that cannot be reached."""
import requests

from app import seal
from app.extensions import db
from app.models import SealAnchor, Survey
from tests import tsa_fake
from tests.exports.test_seal import sealed_survey, tsa  # noqa: F401
from tests.privacy.test_erasure import take

FETCH = {"X-Requested-With": "fetch"}
URL = "/surveys/1/export/seal-chain/check"


def test_a_chain_without_entries_says_there_is_nothing_to_check(app, client):
    app.config["SEAL_DEFAULT"] = True
    client.post("/surveys/new", data={"title": "Neu", "languages": ["de"], "default_language": "de"})
    with app.app_context():
        survey = db.session.get(Survey, 1)
        assert survey.seal and not seal.has_chain(survey)
    page = client.get("/surveys/1/data").data.decode()
    assert 'id="seal"' in page and "Jetzt prüfen" in page
    assert "Noch keine Einträge" in page and "Noch nicht gestempelt" not in page
    r = client.post(URL, headers=FETCH)
    assert r.status_code == 200 and "Keine Einträge, nichts zu prüfen" in r.data.decode()
    assert "Keine Einträge, nichts zu prüfen" in client.post(URL, follow_redirects=True).data.decode()
    # the download answered 404 as well (RWTH log 2026-10-19): no button yet, and the address explains itself
    assert "/surveys/1/export/seal-chain.zip" not in page and "noch keine Einträge" in page
    r = client.get("/surveys/1/export/seal-chain.zip")
    assert r.status_code == 302
    assert "es gibt noch nichts herunterzuladen" in client.get(r.headers["Location"]).data.decode()


def test_a_survey_without_the_chain_still_has_no_check(app, client):
    app.config["SEAL_DEFAULT"] = False
    client.post("/surveys/new", data={"title": "Neu", "languages": ["de"], "default_language": "de"})
    assert client.post(URL, headers=FETCH).status_code == 404


def test_an_unstamped_chain_is_checked_and_stamped_at_the_check(app, client, survey, tsa):  # noqa: F811
    uid = sealed_survey(app, client)
    take(app.test_client(), uid, "a")
    take(app.test_client(), uid, "b")
    with app.app_context():
        db.session.query(SealAnchor).delete()
        db.session.commit()
    r = client.post(URL, headers=FETCH)
    block = r.data.decode()
    assert r.status_code == 200 and "Siegel intakt" in block and "Alle 3 Einträge" in block
    assert "vom Zeitstempeldienst des DFN" in block                     # stamped at the check (as at the download)


def test_an_unreachable_timestamp_service_is_named(app, client, survey, tsa, monkeypatch):  # noqa: F811
    uid = sealed_survey(app, client)
    monkeypatch.setattr(requests, "post", tsa_fake.down)
    take(app.test_client(), uid, "a")
    r = client.post(URL, headers=FETCH)
    block = r.data.decode()
    assert r.status_code == 200 and "Siegel intakt" in block              # the chain is checked all the same
    assert "nicht erreichbar" in block and "tsa.test" in block and "ausgehenden Verbindungen" in block
    page = client.post(URL, follow_redirects=True).data.decode()
    assert "nicht erreichbar" in page


def test_no_timestamp_service_set_is_said(app, client, survey):
    uid = sealed_survey(app, client)
    app.config["TSA_URL"] = ""
    take(app.test_client(), uid, "a")
    block = client.post(URL, headers=FETCH).data.decode()
    assert "Siegel intakt" in block and "Kein Zeitstempeldienst eingestellt" in block


def test_an_error_in_the_check_answers_with_its_own_message(app, client, survey, monkeypatch):
    uid = sealed_survey(app, client)
    take(app.test_client(), uid, "a")

    def broken(_survey):
        raise RuntimeError("boom")

    monkeypatch.setattr(seal, "check", broken)
    r = client.post(URL, headers=FETCH)
    assert r.status_code == 500 and r.headers.get("X-Seal-Result") == "1"
    assert "auf dem Server abgebrochen" in r.data.decode() and "<html" not in r.data.decode()


def test_the_script_shows_the_answer_of_the_check_also_on_an_error():
    """app.js: a reply marked X-Seal-Result is shown as it is; the generic text only when no answer came."""
    from pathlib import Path

    js = (Path(__file__).resolve().parents[2] / "app" / "static" / "app.js").read_text(encoding="utf-8")
    part = js.split("[data-seal-check]")[1].split("})();")[0]
    assert "X-Seal-Result" in part and "keine Antwort" in part
