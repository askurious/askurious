from datetime import timedelta, timezone

from app import diary
from app.extensions import db
from app.models import Response, Survey, utcnow

def test_app_overview_keeps_contact_and_confirmed_prompt_keys(app, client):
    client.post("/diary/new", data={"title": "App UI", "language": "de"})
    with app.app_context():
        survey = db.session.scalar(db.select(Survey))
        config = dict(survey.consent_item.config)
        config["contact_email"] = "team@example.org"
        survey.consent_item.config = config
        db.session.commit()
        code = diary.test_code(survey)
        assert diary.public_json(survey, "de")["contact"] == "team@example.org"
    reply = client.post("/api/diary/v1/enrol", json={"code": code, "lang": "de"}).get_json()
    headers = {"Authorization": "Bearer " + reply["secret"]}
    with app.app_context():
        from app.models import Enrolment
        person = db.session.scalar(db.select(Enrolment))
        due = utcnow().replace(second=0, microsecond=0) - timedelta(minutes=5)
        row = Response(survey_id=survey.id, enrolment_id=person.id, pid=person.pid, language="de", status=Response.COMPLETED,
                       occasion={"part": "m1", "due": due.isoformat()}, source="test")
        db.session.add(row)
        db.session.commit()
    result = client.post("/api/diary/v1/study", json={}, headers=headers).get_json()
    assert result["contact"] == "team@example.org"
    assert result["completed_prompts"] == [f"m1|{int(due.replace(tzinfo=timezone.utc).timestamp() * 1000)}"]
    # the moment from which the prompts count (G1, audit 2026-10-18): none before the person is active
    assert "prompts_from" in result and result["prompts_from"] is None
