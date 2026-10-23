"""Rights package of rechte-uebersicht-konzept.md (Ralf, 2026-10-09): H3 who may bring someone in, H4 sharing only
with people who can sign in, H7 a site admin makes a member lead, H9 the words of "Ansehen" in an interview guide."""
from app.extensions import db
from app.models import Design, Survey, SurveyMember, User, UserLogin, WorkGroup, WorkGroupMember
from app import theme
from tests.conftest import allow
from tests.conftest import CREATE_ALL

ME = "0000-0000-0000-001X"       # site admin (tests/conftest.py)
OTHER = "0000-0002-1825-0097"
THIRD = "0000-0000-0000-0124"
FOURTH = "0000-0000-0000-0239"


def _as(client, orcid, name):
    client.get(f"/auth/dev-login?orcid={orcid}&name={name}")
    client.post("/terms/", data={"accept": "1"})


def _group_led_by_other(client):
    """A group made by the site admin, led by OTHER, an account that only came in through it (no right to create)."""
    client.post("/groups/new", data={"name": "Lehrstuhl"})
    with client.application.app_context():
        gid = db.session.scalar(db.select(WorkGroup.id).order_by(WorkGroup.id.desc()))
    client.post(f"/groups/{gid}/members", data={"orcid": OTHER, "name": "Kollegin", "role": "lead"})
    _as(client, OTHER, "Kollegin")
    return gid


def test_invited_lead_adds_only_people_who_can_sign_in(client):
    gid = _group_led_by_other(client)
    page = client.post(f"/groups/{gid}/members", data={"orcid": THIRD, "name": "Fremd"}, follow_redirects=True).data.decode()
    assert "kann sich noch nicht bei Askurious anmelden" in page
    with client.application.app_context():
        assert db.session.scalar(db.select(WorkGroupMember).filter_by(group_id=gid, orcid=THIRD)) is None
    # someone who can sign in by themselves is fine
    allow(client.application, THIRD, keep=True)
    page = client.post(f"/groups/{gid}/members", data={"orcid": THIRD, "name": "Dritte"}, follow_redirects=True).data.decode()
    assert "Dritte ist jetzt in der Gruppe" in page


def test_invited_survey_admin_adds_only_people_who_can_sign_in(client, survey):
    client.post(f"/surveys/{survey}/members", data={"orcid": OTHER, "name": "Kollegin", "role": "admin"})
    _as(client, OTHER, "Kollegin")
    page = client.post(f"/surveys/{survey}/members", data={"orcid": THIRD, "name": "Fremd", "role": "viewer"},
                       follow_redirects=True).data.decode()
    assert "kann sich noch nicht bei Askurious anmelden" in page
    with client.application.app_context():
        assert db.session.scalar(db.select(SurveyMember).filter_by(survey_id=survey, orcid=THIRD)) is None
    # with the right to create surveys the same person brings anyone in, as before
    with client.application.app_context():
        user = db.session.scalar(db.select(User).filter_by(orcid=OTHER))
        row = db.session.get(UserLogin, user.id) or UserLogin(user_id=user.id)
        row.kinds = CREATE_ALL
        db.session.add(row)
        db.session.commit()
    page = client.post(f"/surveys/{survey}/members", data={"orcid": THIRD, "name": "Fremd", "role": "viewer"},
                       follow_redirects=True).data.decode()
    assert "Fremd ist jetzt beteiligt" in page


def test_mail_invitation_needs_the_right_to_create(client, survey):
    from app import auth, invites

    client.post(f"/surveys/{survey}/members", data={"orcid": OTHER, "name": "Kollegin", "role": "admin"})
    _as(client, OTHER, "Kollegin")
    with client.application.test_request_context():
        other = db.session.scalar(db.select(User).filter_by(orcid=OTHER))
        assert invites.create("neu@example.org", "editor", other, survey=db.session.get(Survey, survey)) == auth.not_admitted()
        me = db.session.scalar(db.select(User).filter_by(orcid=ME))
        assert auth.may_admit(me) and not auth.may_admit(other) and auth.may_admit(other, ME)


def test_sharing_with_a_person_who_cannot_sign_in_is_refused(client):
    with client.application.app_context():
        owner = db.session.scalar(db.select(User).filter_by(orcid=ME))
        design = Design(owner_id=owner.id, name="Hausfarben", data=theme.standard())
        db.session.add(design)
        db.session.commit()
        did = design.id
    page = client.post(f"/designs/{did}/share", data={"orcid": FOURTH}, follow_redirects=True).data.decode()
    assert "Teilen allein gibt kein Anmelderecht" in page
    allow(client.application, FOURTH, keep=True)
    assert "Design geteilt" in client.post(f"/designs/{did}/share", data={"orcid": FOURTH}, follow_redirects=True).data.decode()


def test_site_admin_makes_a_member_lead(client):
    client.post("/groups/new", data={"name": "Verwaist"})
    with client.application.app_context():
        gid = db.session.scalar(db.select(WorkGroup.id).order_by(WorkGroup.id.desc()))
    client.post(f"/groups/{gid}/members", data={"orcid": OTHER, "name": "Kollegin"})
    with client.application.app_context():
        mid = db.session.scalar(db.select(WorkGroupMember.id).filter_by(group_id=gid, orcid=OTHER))
    assert "Zur Leitung machen" in client.get("/admin/groups").data.decode()
    page = client.post(f"/admin/groups/{gid}/lead", data={"member_id": mid}, follow_redirects=True).data.decode()
    assert "Kollegin leitet jetzt die Gruppe" in page
    with client.application.app_context():
        assert db.session.get(WorkGroupMember, mid).role == "lead"
    # not for anyone else
    _as(client, OTHER, "Kollegin")
    assert client.post(f"/admin/groups/{gid}/lead", data={"member_id": mid}).status_code == 403


def test_view_role_of_an_interview_guide_says_it_saves_nothing():
    roles = SurveyMember.ROLES_BY_KIND["interview"]["viewer"][1]
    assert "ohne zu speichern" in roles and "Interviews mit der Interviewansicht führen" not in roles
