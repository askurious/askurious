"""One setup wizard for every kind of survey (app/wizard.py, Ralf 2026-10-27): the steps of a diary survey and an
interview guide, what each step saves, the overview with the box "Für Datenarchive" fitting the kind, and the two
ways on at the end (into the survey, or the kind's AI assistant)."""
from app import ddi, diary, study_plan, wizard
from app.extensions import db
from app.models import Survey
from tests.conftest import experiments_on


def test_steps_of_each_kind():
    assert wizard.keys_of("survey") == ["projekt", "studienplan", "team", "ablauf", "einwilligung", "gestaltung", "fertig"]
    assert wizard.keys_of("diary") == ["projekt", "studienplan", "team", "zeitplan", "einwilligung", "gestaltung", "fertig"]
    assert wizard.keys_of("interview") == ["projekt", "studienplan", "team", "fertig"]
    assert dict((k, s) for k, _, s, _ in wizard.STEPS)["fertig"] == "Alle Angaben auf einen Blick"


def test_diary_survey_through_the_wizard(app, client):
    experiments_on(client, "diary")
    r = client.post("/diary/new", data={"title": "Lernen im Alltag", "language": "de", "description": "Wie lernen Studierende?",
                                        "keywords": "Lernen, Alltag", "demographics": "1", "exit": "1"})
    step = r.headers["Location"]
    assert step.endswith("/setup/researchdesign")
    with app.app_context():
        s = db.session.scalar(db.select(Survey).filter_by(title="Lernen im Alltag"))
        assert s.kind == "diary" and s.setup_step == "studienplan" and s.description == "Wie lernen Studierende?"
        assert study_plan.archive_of(s.study_plan.get("archive"))["keywords"] == ["Lernen", "Alltag"]
        sid, code = s.id, s.code
    base = step.rsplit("/", 1)[0] + "/"
    page = client.get(step).data.decode()
    assert "Schritt 2 von 7" in page and "Zeitplan und Beitritt" in page and "Später weiter und jetzt zum Zeitplan" in page
    client.post(step, data={"recruitment": "convenience", "population": "Studierende", "country": "DE",
                            "region": "Nordrhein-Westfalen", "autosave": "1"})
    page = client.get(base + "schedule").data.decode()
    assert 'name="join" value="personal"' in page
    client.post(base + "schedule", data={"join": "personal", "persons": "3", "autosave": "1"})
    with app.app_context():
        s = db.session.get(Survey, sid)
        sample = s.study_plan["sample"]
        assert (sample["recruitment"], sample["population"], sample["region"]) == ("convenience", "Studierende", "Nordrhein-Westfalen")
        assert diary.invite_only(s) and len(diary.invites(s)) == 3
    for key in ("team", "consent", "design"):
        assert client.get(base + key).status_code == 200
    done = client.get(base + "done").data.decode()
    assert "Für Datenarchive" in done and "Als Nächstes: Zeitplan und Fragebogen" in done and "persönliche Codes" in done
    assert 'value="ai"' not in done  # no AI assistant for diary surveys yet
    r = client.post(base + "done", data={"wizard": "next"})
    assert r.headers["Location"].endswith("/schedule") and "/setup/" not in r.headers["Location"]
    with app.app_context():
        assert db.session.get(Survey, sid).setup_step is None


def test_interview_guide_through_the_wizard(app, client):
    r = client.post("/guide/new", data={"title": "Pflegende Angehörige", "language": "de", "template": "leer",
                                        "order": "free", "description": "Gespräche über den Alltag."})
    step = r.headers["Location"]
    base = step.rsplit("/", 1)[0] + "/"
    with app.app_context():
        s = db.session.scalar(db.select(Survey).filter_by(title="Pflegende Angehörige"))
        sid, code, member = s.id, s.code, s.members[0].id
    page = client.get(step).data.decode()
    assert "Schritt 2 von 4" in page and 'name="target"' in page and 'name="mode"' in page and "Einwilligung" not in page
    client.post(step, data={"target": "fachleute", "mode": "video", "recruitment": "purposive", "population": "Pflegedienste",
                            "country": "DE", "autosave": "1"})
    client.post(base + "team", data={f"aff_{member}": "RWTH Aachen University", "autosave": "1"})
    assert client.get(base + "consent").status_code == 404  # a guide has no such step
    with app.app_context():
        s = db.session.get(Survey, sid)
        assert (s.interview["target"], s.interview["mode"]) == ("fachleute", "video")
        assert s.members[0].affiliation == "RWTH Aachen University"
        rows = {r["en"]: r for r in ddi.readiness(s, wizard.keys_of("interview"))}
        assert "Licence" not in rows and "Funding" not in rows and "Time method" not in rows
        assert rows["Publisher"]["state"] == "ok"
    done = client.get(base + "done").data.decode()
    assert "Für Datenarchive" in done and "Als Nächstes: den Leitfaden schreiben" in done and "Zum Leitfaden" in done
    assert "Mit dem KI-Assistenten Leitfragen entwickeln" in done and 'value="ai"' in done
    r = client.post(base + "done", data={"wizard": "ai"})
    assert "/develop" in r.headers["Location"] or "/ai" in r.headers["Location"]
    with app.app_context():
        assert db.session.get(Survey, sid).setup_step is None


def test_questionnaire_overview_offers_the_scale_suggestions(app, client, survey):
    done = client.get("/surveys/1/setup/done").data.decode()
    assert "Mit KI passende Skalen vorschlagen lassen" in done and "Als Nächstes: den Fragebogen gestalten" in done
    r = client.post("/surveys/1/setup/done", data={"wizard": "ai"})
    assert r.headers["Location"].endswith("#ai-scales")
    assert 'id="ai-scales"' in client.get(r.headers["Location"].split("#")[0]).data.decode()
