"""Coscine (app/coscine.py, experimental), Coscine-Ausbau 2026-10-20, package D: inviting the people of a survey into
the Coscine project (K3, roles after E5, whole groups E6) and the publication request (K5). Coscine's API is replaced
by FakeCoscine of test_coscine.py."""
from app.extensions import db
from app.models import Survey, SurveyGroup, SurveyMember, User, UserLogin, WorkGroup, WorkGroupMember
from tests.exports.test_coscine import GUEST, MEMBER, connect, fake, on  # noqa: F401 - fake is a fixture


def _target(app, uploads=True):
    with app.app_context():
        s = db.session.get(Survey, 1)
        entry = {"folder": "askurious/x/2026-10-20_100000", "files": [{"name": "data.csv", "size": 3}], "shown": "20.10.2026 10:00",
                 "by": "Ralf", "project": "Akzeptanzstudie", "resource": "Befragungsdaten", "project_id": "p1",
                 "resource_id": "r1", "pid": "21.11102/r1"}
        s.coscine = {"project": "p1", "resource": "r1", "uploads": [entry] if uploads else []}
        db.session.commit()


def _team(app):
    """Julia edits directly, Bob only views through the group "Lehrstuhl", Olga signed in with ORCID only."""
    with app.app_context():
        s = db.session.get(Survey, 1)
        db.session.add(UserLogin(user_id=1, email="ralf@example.org"))
        people = {}
        for n, (name, mail) in enumerate((("Julia", "julia@example.org"), ("Bob", "bob@example.org"), ("Olga", ""))):
            u = User(orcid=f"0000-0000-0001-000{n}", name=name)
            db.session.add(u)
            db.session.flush()
            if mail:
                db.session.add(UserLogin(user_id=u.id, email=mail))
            people[name] = u
        s.members.append(SurveyMember(user_id=people["Julia"].id, name="Julia", role="editor"))
        s.members.append(SurveyMember(user_id=people["Olga"].id, name="Olga", role="viewer"))
        group = WorkGroup(name="Lehrstuhl")
        group.members.append(WorkGroupMember(user_id=people["Bob"].id, name="Bob", role="member"))
        db.session.add(group)
        db.session.flush()
        db.session.add(SurveyGroup(survey_id=1, group_id=group.id, role="viewer"))
        db.session.commit()
        return {k: v.id for k, v in people.items()}, group.id


def test_people_page_lists_both_sides_and_invites(app, client, survey, fake):
    on(client)
    connect(client, fake)
    _target(app)
    ids, group_id = _team(app)
    page = client.get("/coscine/survey/1/people").data.decode()
    assert "Mitglied (Owner)" in page                                   # Ralf is in the project already
    assert "über Lehrstuhl" in page and "keine E-Mail-Adresse in Askurious" in page
    assert f'name="person" value="{ids["Olga"]}" disabled' in page   # ORCID only: cannot be invited
    r = client.post("/coscine/survey/1/people", data={"person": [str(ids["Julia"]), str(ids["Olga"])]},
                    follow_redirects=True)
    assert "In Coscine eingeladen: Julia" in r.data.decode()
    assert [(i["email"], i["role"]["id"]) for i in fake.invited] == [("julia@example.org", MEMBER)]  # editor: Member
    client.post("/coscine/survey/1/people", data={"gruppe": str(group_id)})
    assert fake.invited[-1]["email"] == "bob@example.org" and fake.invited[-1]["role"]["id"] == GUEST  # viewer: Guest
    page = client.get("/coscine/survey/1/people").data.decode()
    assert page.count("eingeladen bis") == 2 and "Einladung zurücknehmen" in page
    r = client.post("/coscine/survey/1/people", data={"person": str(ids["Julia"])}, follow_redirects=True)
    assert "Niemand zum Einladen ausgewählt" in r.data.decode() and len(fake.invited) == 2  # never twice
    client.post("/coscine/survey/1/people/withdraw", data={"einladung": "i1"})
    assert [i["email"] for i in fake.invited] == ["bob@example.org"]


def test_people_page_needs_a_target(app, client, survey, fake):
    on(client)
    connect(client, fake)
    r = client.get("/coscine/survey/1/people", follow_redirects=True)
    assert "Erst ein Ziel in Coscine wählen" in r.data.decode() and not fake.invited


def test_publication_request(app, client, survey, fake):
    on(client)
    connect(client, fake)
    _target(app)
    assert "Veröffentlichung beantragen" in client.get("/surveys/1/data").data.decode()
    page = client.get("/coscine/survey/1/publish").data.decode()
    assert "RWTH Publications" in page and "Bitte um Veröffentlichung der Ressource „Befragungsdaten“ mit DOI." in page
    r = client.post("/coscine/survey/1/publish", data={"dienst": "https://ror.org/04xfq0f34", "nachricht": "Bitte mit DOI."},
                    follow_redirects=True)
    assert "Veröffentlichung bei RWTH Publications beantragt" in r.data.decode()
    assert fake.requests == [{"publicationServiceRorId": "https://ror.org/04xfq0f34", "resourceIds": ["r1"],
                              "message": "Bitte mit DOI."}]
    assert "Veröffentlichung beantragt am" in client.get("/surveys/1/data").data.decode()
    r = client.post("/coscine/survey/1/publish", data={"dienst": "https://ror.org/fremd", "nachricht": "x"})
    assert r.status_code == 400 and len(fake.requests) == 1


def test_publication_request_blocked_without_clearance(app, client, survey, fake, monkeypatch):
    from app import coscine

    on(client)
    connect(client, fake)
    _target(app)
    monkeypatch.setattr(coscine, "publication_blocked", lambda s: "Für 1 Bild fehlen dokumentierte Rechte.")
    page = client.get("/coscine/survey/1/publish").data.decode()
    assert "lässt sich nicht veröffentlichen" in page and "disabled" in page
    r = client.post("/coscine/survey/1/publish", data={"dienst": "https://ror.org/04xfq0f34", "nachricht": "x"})
    assert r.status_code == 400 and not fake.requests
