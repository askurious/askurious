"""Required questions of the Kurzfragebogen in a stored interview (pretest 2026-10-22: "Pflicht" set, yet "Weiter"
went on without an answer). The form keeps saving what is given; the server names the required questions still
open, and "Interview beenden" holds until they are answered, as a page of a questionnaire does."""
from app.extensions import db
from app.models import Response, Survey
from tests.interview.test_interview import make
from tests.interview.test_interview_data import start


def _short_items(app, sid):
    from app import interview

    with app.app_context():
        survey = db.session.get(Survey, sid)
        short = interview.shown_form(survey, "short")
        age = next(i for i in short.items if i.type == "text_input")
        gender = next(i for i in short.items if i.type == "single_choice")
        for i in short.items:
            i.answer_mode = "required" if i.id == age.id else "optional"
        db.session.commit()
        return age.id, gender.id, gender.config["options"][0]["code"]


def test_form_save_names_open_required_questions(app, client):
    code, sid = make(app, client)
    rid = start(app, client, code)
    age, gender, option = _short_items(app, sid)
    r = client.post(f"/guide/{code}/interviews/{rid}/form", data={"part": "short", f"i{gender}": option})
    assert r.json["ok"] and list(r.json["missing"]) == [str(age)]          # saved anyway, the open one named
    with app.app_context():
        assert db.session.get(Response, rid).answers[str(gender)] == option
    r = client.post(f"/guide/{code}/interviews/{rid}/form", data={"part": "short", f"i{gender}": option, f"i{age}": "57"})
    assert r.json["missing"] == {}


def test_end_holds_while_a_required_question_is_open(app, client):
    code, sid = make(app, client)
    rid = start(app, client, code)
    age, gender, option = _short_items(app, sid)
    r = client.post(f"/guide/{code}/interviews/{rid}/end")
    assert r.status_code == 302 and f"interview={rid}" in r.headers["Location"]
    with app.app_context():
        assert db.session.get(Response, rid).status != Response.COMPLETED
    page = client.get(r.headers["Location"]).data.decode()
    assert "noch Pflichtfragen offen" in page
    client.post(f"/guide/{code}/interviews/{rid}/form", data={"part": "short", f"i{age}": "57"})
    assert "Löschcode" in client.post(f"/guide/{code}/interviews/{rid}/end").data.decode()
    with app.app_context():
        assert db.session.get(Response, rid).status == Response.COMPLETED
