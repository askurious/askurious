"""A new live vote starts with the seal chain on but without an entry (SEAL_DEFAULT). Its first answer begins the
chain and names the survey in seal_chain, from the participants' side. Their database role could not read that table:
every first answer ended in a server error and the phones said "Nicht gesendet" (live quiz, 2026-10-23)."""
from app import participant_grants
from app.extensions import db
from app.models import Response, Survey
from tests.helpers import participant_sql
from tests.live.test_live import add, make, send, steer


def test_first_answer_of_a_new_vote_with_the_seal_chain(app, client):
    from app import seal

    app.config["SEAL_DEFAULT"] = True
    try:
        code, sid, block = make(app, client, "Quiz mit Siegel")
    finally:
        app.config["SEAL_DEFAULT"] = False
    q = add(app, client, sid, block, "single_choice", question="Hauptstadt?", options=["Berlin", "Bonn"])
    with app.app_context():
        survey = db.session.get(Survey, sid)
        assert survey.seal and not seal.has_chain(survey)  # on, no entry yet
    steer(client, code, action="start")
    phone = app.test_client()
    phone.get(f"/live/{code}")
    participant_sql.start()
    try:
        r = send(phone, code, {f"i{q}": "1"})
    finally:
        seen = participant_sql.stop()
    assert r.status_code == 200 and r.json["ok"], r.data
    assert ("seal_chain", "INSERT") in seen  # the chain begins with this answer
    assert participant_sql.violations(seen) == {}
    with app.app_context():
        survey = db.session.get(Survey, sid)
        assert seal.has_chain(survey)
        assert db.session.scalar(db.select(db.func.count(Response.id)).filter_by(survey_id=sid)) == 1


def test_the_participants_may_begin_a_chain():
    assert participant_grants.allowed("seal_chain") == ("SELECT", "INSERT")
    assert "seal_chain" not in participant_grants.NOT_GRANTED
