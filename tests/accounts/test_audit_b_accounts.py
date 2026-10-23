"""Audit 2026-10-05, package B: accounts, co-owners and Coscine.

S20   only accounts that signed in and are not blocked are offered as the next admin (or lead)
L-9   the automatic upload to Coscine runs only with the token of a person who is still admin of the survey, and
      goes off when that person leaves or is removed
L-11  deleting an account turns its automatic upload off and takes its name out of survey.coscine.auto
"""
from app import accounts, coscine
from app.extensions import db
from app.models import Survey, SurveyMember, User, UserLogin, WorkGroup, WorkGroupMember
from tests.accounts.test_accounts import ME, OTHER, THIRD, _as, _delete, _survey, _user, other  # noqa: F401 (fixture)
from tests.conftest import allow

FOURTH = "0000-0000-0000-0239"


def _auto(app, sid, orcid, name="Kollegin"):
    with app.app_context():
        s = db.session.get(Survey, sid)
        s.coscine = {"project": "p1", "resource": "r1", "uploads": [{"by": name, "folder": "f"}],
                     "auto": {"on": True, "user_id": _user(orcid).id, "name": name}}
        db.session.commit()


def test_s20_only_accounts_that_can_take_over_are_offered(app, client, other):
    allow(app, OTHER, THIRD, FOURTH)
    _as(app.test_client(), THIRD, "Gesperrt")
    sid = _survey(other, "Nachfolge")
    for orcid, name in ((THIRD, "Gesperrt"), (FOURTH, "Nie angemeldet"), (ME, "Philipp")):
        other.post(f"/surveys/{sid}/members", data={"orcid": orcid, "name": name, "role": "editor"})
    with app.app_context():
        db.session.merge(UserLogin(user_id=_user(THIRD).id, status=UserLogin.BLOCKED))
        group = WorkGroup(name="Gruppe")
        db.session.add(group)
        db.session.flush()
        for orcid, role in ((OTHER, "lead"), (FOURTH, "member"), (ME, "member")):
            group.members.append(WorkGroupMember(orcid=orcid, role=role))
        db.session.commit()
        p = accounts.plan(_user(OTHER))
        assert [m.orcid for m in p["successor"][0]["candidates"]] == [ME] and not p["successor"][0]["unproven"]
        assert [m.orcid for m in p["group_successor"][0]["candidates"]] == [ME, FOURTH]
        blocked = db.session.scalar(db.select(SurveyMember.id).filter_by(survey_id=sid, orcid=THIRD))
    page = other.get("/account/delete").data.decode()
    assert "Philipp (bisher Bearbeiten)" in page and "Gesperrt (bisher" not in page and "Nie angemeldet (bisher" not in page
    _delete(other, **{f"survey_{sid}": str(blocked)})  # a forged choice of a blocked account is not taken
    with app.app_context():
        roles = {m.orcid: m.role for m in db.session.get(Survey, sid).members}
        assert roles[ME] == "admin" and roles[THIRD] == "editor"


def test_s20_without_anyone_able_all_are_named_with_a_note(app, client, other):
    sid = _survey(other, "Allein")
    other.post(f"/surveys/{sid}/members", data={"orcid": FOURTH, "name": "Nie angemeldet", "role": "editor"})
    with app.app_context():
        entry = accounts.plan(_user(OTHER))["successor"][0]
        assert entry["unproven"] and [m.orcid for m in entry["candidates"]] == [FOURTH]
    assert "Keine der übrigen Personen hat sich schon angemeldet" in other.get("/account/delete").data.decode()


def test_l9_no_upload_with_the_token_of_someone_no_longer_admin(app, client, other, monkeypatch):
    sid = _survey(other, "Coscine")
    other.post(f"/surveys/{sid}/members", data={"orcid": ME, "name": "Philipp", "role": "admin"})
    _auto(app, sid, OTHER)
    with app.app_context():
        member = db.session.scalar(db.select(SurveyMember).filter_by(survey_id=sid, orcid=OTHER))
        member.role = "editor"  # demoted by the other admin
        db.session.commit()
        monkeypatch.setattr(coscine, "client_for", lambda user: (_ for _ in ()).throw(AssertionError("token used")))
        ok, text = coscine.auto_store(db.session.get(Survey, sid), "prereg:v1")
        s = db.session.get(Survey, sid)
        assert not ok and "nicht mehr Admin" in text
        assert s.coscine["auto"]["on"] is False and "user_id" not in s.coscine["auto"] and "name" not in s.coscine["auto"]
        assert coscine.auto_store(s, "prereg:v2") is None  # off from now on


def test_l9_leaving_or_being_removed_turns_the_automatic_upload_off(app, client, other):
    sid = _survey(other, "Verlassen")
    other.post(f"/surveys/{sid}/members", data={"orcid": ME, "name": "Philipp", "role": "admin"})
    _auto(app, sid, ME, "Philipp")
    with app.app_context():
        mine = db.session.scalar(db.select(SurveyMember.id).filter_by(survey_id=sid, orcid=ME))
    client.post(f"/surveys/{sid}/members/{mine}/remove")  # leaves
    with app.app_context():
        assert db.session.get(Survey, sid).coscine["auto"] == {"on": False}
    other.post(f"/surveys/{sid}/members", data={"orcid": ME, "name": "Philipp", "role": "admin"})
    _auto(app, sid, OTHER)
    with app.app_context():
        mine = db.session.scalar(db.select(SurveyMember.id).filter_by(survey_id=sid, orcid=ME))
    _auto(app, sid, ME, "Philipp")
    other.post(f"/surveys/{sid}/members/{mine}/remove")  # removed by the other admin
    with app.app_context():
        assert db.session.get(Survey, sid).coscine["auto"] == {"on": False}


def test_l11_deleting_the_account_turns_the_automatic_upload_off(app, client, other):
    sid = _survey(other, "Bleibt")
    other.post(f"/surveys/{sid}/members", data={"orcid": ME, "name": "Philipp", "role": "admin"})
    _auto(app, sid, OTHER, "Kollegin Name")
    _delete(other)
    with app.app_context():
        assert _user(OTHER) is None
        state = db.session.get(Survey, sid).coscine
        assert state["auto"] == {"on": False} and "Kollegin Name" not in repr(state["auto"])
        assert state["uploads"][0]["by"] == "Kollegin Name"  # the list of uploads stays as documentation (decided)
