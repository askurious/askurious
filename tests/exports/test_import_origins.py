"""Reading in a file from the page of a live vote or an interview guide (wizard.import_file with ?origin=): the page
offers it, and the preview and a refusal lead back to the page the file came from."""
import io
import json
import re

from app.extensions import db
from app.models import Survey
from tests.interview import test_interview
from tests.live import test_live


def _file(client, path):
    return json.loads(client.get(f"/surveys/{path}/export/survey.json").data)


def test_new_pages_offer_reading_in_a_file(client):
    """The same three cards on every page that makes something new (Ralf, 2026-10-12): the file under "Aus einer Datei"."""
    from tests.conftest import experiments_on

    experiments_on(client, "diary")
    for page, origin in (("/presenter/new", "live"), ("/guide/new", "leitfaden"), ("/diary/new", "tagebuch")):
        html = client.get(page).data.decode()
        assert html.count('<a class="start-card') == 3 and 'id="import"' not in html
        html = client.get(page + "?start=datei").data.decode()
        assert 'id="import"' in html and "Aus einer Datei anlegen" in html and 'aria-current="page"' in html
        assert f'action="/surveys/new/import?origin={origin}"' in html
    assert 'action="/surveys/new/import"' in client.get("/surveys/new?start=datei").data.decode()  # the questionnaire as before


def test_refusal_and_preview_lead_back_to_the_page(app, client):
    r = client.post("/surveys/new/import?origin=live", data={"file": (io.BytesIO(b"kein json"), "x.json")},
                    content_type="multipart/form-data")
    assert r.headers["Location"].endswith("/presenter/new?start=datei#import")
    r = client.post("/surveys/new/import?origin=leitfaden", data={})
    assert r.headers["Location"].endswith("/guide/new?start=datei#import")
    r = client.post("/surveys/new/import?origin=anderes", data={})  # an unknown page: the questionnaire wizard
    assert r.headers["Location"].endswith("/surveys/new?start=datei#import")

    code, sid, _ = test_live.make(app, client)
    data = _file(client, sid)
    page = client.post("/surveys/new/import?origin=live", data={"file": (io.BytesIO(json.dumps(data).encode()), "v.json")},
                       content_type="multipart/form-data").data.decode()
    assert "Live-Abstimmung" in page and 'href="/presenter/new?start=datei#import"' in page
    assert 'action="/surveys/new/import?origin=live"' in page
    token = re.search(r'name="token" value="([^"]+)"', page).group(1)
    r = client.post("/surveys/new/import?origin=live", data={"token": token, "title-0": "Ringvorlesung 2"})
    assert r.status_code == 302
    with app.app_context():
        new = db.session.scalar(db.select(Survey).filter_by(title="Ringvorlesung 2"))
        assert new is not None and new.live is not None


def test_interview_guide_from_its_own_page(app, client):
    code, sid = test_interview.make(app, client)
    data = _file(client, sid)
    page = client.post("/surveys/new/import?origin=leitfaden",
                       data={"file": (io.BytesIO(json.dumps(data).encode()), "l.json")},
                       content_type="multipart/form-data").data.decode()
    assert "Interviewleitfaden" in page and 'href="/guide/new?start=datei#import"' in page
    token = re.search(r'name="token" value="([^"]+)"', page).group(1)
    client.post("/surveys/new/import?origin=leitfaden", data={"token": token, "title-0": "Zweiter Leitfaden"})
    with app.app_context():
        new = db.session.scalar(db.select(Survey).filter_by(title="Zweiter Leitfaden"))
        assert new is not None and new.interview is not None


def test_templates_on_their_own_card(app, client):
    """"Aus einer Vorlage" on the pages of a live vote, an interview guide and a diary study (Ralf, 2026-10-12)."""
    from app import diary
    from tests.conftest import experiments_on

    experiments_on(client, "diary")
    # live: the questions of the template at once, then the step "Fertig"
    html = client.get("/presenter/new?start=vorlage").data.decode()
    assert "Feedback zur Sitzung" in html and 'name="template" value="muddiest"' in html and "wizard-steps" not in html
    r = client.post("/presenter/new", data={"template": "feedback", "title": "Sitzung 3", "language": "de-du"})
    assert r.headers["Location"].endswith("/setup/done")
    with app.app_context():
        s = db.session.scalar(db.select(Survey).filter_by(title="Sitzung 3"))
        assert s.setup_step == "fertig" and len([i for b in s.blocks for i in b.items]) == 4
    # interview: empty without the choice of a template, the template card without "Leer"
    html = client.get("/guide/new").data.decode()
    assert 'name="template" value="leer"' in html and 'value="witzel"' not in html
    html = client.get("/guide/new?start=vorlage").data.decode()
    assert 'value="witzel" checked' in html and 'value="leer"' not in html and "Witzel" in html
    # diary: four designs, each with entry and exit survey and its schedule
    html = client.get("/diary/new?start=vorlage").data.decode()
    assert all(f'name="template" value="{k}"' in html for k in diary.DIARY_TEMPLATES) and "Bolger" in html
    for key, title in (("signal", "ESM"), ("ereignis", "Melden")):
        client.post("/diary/new", data={"template": key, "title": title, "language": "de"})
    with app.app_context():
        esm = db.session.scalar(db.select(Survey).filter_by(title="ESM"))
        module = esm.sampling["modules"][0]
        assert module["trigger"]["t"] == "random" and module["trigger"]["per_day"] == 5 and esm.sampling["period_days"] == 7
        parts = {(b.settings or {}).get("part") for b in esm.blocks}
        assert diary.EXIT in parts and {i.code for b in esm.blocks for i in b.items} >= {"age", "GES_1"}  # gender: RatSWD GES_1
        report = db.session.scalar(db.select(Survey).filter_by(title="Melden")).sampling["modules"][0]["trigger"]
        assert report["t"] == "self" and report["label"] == {"de": "Etwas melden"}
    # the empty diary keeps its ticks
    assert 'name="demographics"' in client.get("/diary/new").data.decode()
