"""Organisation admins delete people (app/org_delete.py, org-personen-loeschen-konzept.md, Ralf 2026-10-18 "alles wie
empfohlen"): marked and blocked at once, deleted by the sweep after 30 days, what belongs to the person alone passes to
a person of the organisation or goes; taken back until then; someone who also belongs elsewhere is removed instead."""
from datetime import timedelta

from app import accounts, org_delete, orgs
from app.extensions import db
from app.models import (DeletedAccount, Design, OrgDeletion, Organisation, Response, Survey, SurveyMember, User,
                        UserLogin, WorkGroup, WorkGroupMember, utcnow)
from tests.accounts.test_organisations import FIFTH, FOURTH, OTHER, THIRD, _as, _group, _org_with_admin
from tests.conftest import allow


def _setup(app, client):
    """An organisation (admin OTHER) with THIRD (to be deleted) and FOURTH (who takes over), both at home there.
    THIRD has a survey with a real participation, a draft and a shared design."""
    org_id, _ = _org_with_admin(app, client)
    allow(app, THIRD, FOURTH, keep=True)
    third = _as(app, THIRD, "Dritte")
    _as(app, FOURTH, "Vierte")
    with app.app_context():
        org = db.session.get(Organisation, org_id)
        orgs.add(org, THIRD, home=True)
        orgs.add(org, FOURTH, home=True)
        db.session.commit()
    third.post("/surveys/new", data={"title": "Mit Daten", "languages": ["de"], "default_language": "de"})
    third.post("/surveys/new", data={"title": "Nur Entwurf", "languages": ["de"], "default_language": "de"})
    with app.app_context():
        tid = db.session.scalar(db.select(User.id).filter_by(orcid=THIRD))
        data, draft = (db.session.scalar(db.select(Survey.id).filter_by(title=t)) for t in ("Mit Daten", "Nur Entwurf"))
        db.session.add(Response(survey_id=data, source="real", status=Response.COMPLETED, pid="1-x", answers={}))
        design = Design(owner_id=tid, name="Hausdesign", org_shared=True, org_id=org_id, data={})
        db.session.add(design)
        db.session.commit()
        ids = {"org": org_id, "third": tid, "fourth": db.session.scalar(db.select(User.id).filter_by(orcid=FOURTH)),
               "data": data, "draft": draft, "design": design.id}
    return ids


def test_marked_blocked_and_deleted_after_thirty_days_with_handover(app, client):
    ids = _setup(app, client)
    other = _as(app, OTHER, "Kollegin")
    base = f"/org/{ids['org']}/people/{ids['third']}"
    page = other.get(base).data.decode()
    assert "Konto löschen …" in page and "gehört nur zu dieser Organisation" in page
    page = other.get(base + "/delete").data.decode()
    assert "Mit Daten" in page and "Nur Entwurf" in page and "Hausdesign" in page and "Vierte" in page
    assert "wie voreingestellt: übergeben" in page and "wie voreingestellt: löschen" in page
    # the name has to match
    other.post(base + "/delete", data={"name": "falsch", "heir": ids["fourth"]})
    with app.app_context():
        assert db.session.get(OrgDeletion, ids["third"]) is None
    # without a preset heir the survey with data cannot pass on: nothing is marked
    r = other.post(base + "/delete", data={"name": "Dritte"})
    assert r.status_code == 422 and "niemand ist gewählt" in r.data.decode()
    r = other.post(base + "/delete", data={"name": "Dritte", "heir": ids["fourth"]}, follow_redirects=True)
    assert "wird am" in r.data.decode()
    with app.app_context():
        row = db.session.get(OrgDeletion, ids["third"])
        assert row is not None and abs((row.due_at - utcnow()) - timedelta(days=30)) < timedelta(minutes=5)
        login = db.session.get(UserLogin, ids["third"])
        assert login.status == UserLogin.BLOCKED and login.block_reason == org_delete.REASON
        user = db.session.get(User, ids["third"])
        assert "zur Löschung vorgemerkt" in accounts.blocked_said(user)
        assert org_delete.run() == 0                                        # not due yet
        assert org_delete.run(utcnow() + timedelta(days=31)) == 1
        assert db.session.get(User, ids["third"]) is None
        assert db.session.scalar(db.select(DeletedAccount.by).filter_by(user_id=ids["third"])) == "org"
        kept = db.session.get(Survey, ids["data"])
        assert kept is not None and kept.member_of(db.session.get(User, ids["fourth"])).role == "admin"
        assert not kept.archived                                           # a researcher of the organisation, not an archive
        assert db.session.get(Survey, ids["draft"]) is None                 # drafts go by default
        assert db.session.get(Design, ids["design"]).owner_id == ids["fourth"]  # shared: passes on
        assert db.session.get(OrgDeletion, ids["third"]) is None


def test_taken_back_and_the_unblock_waits_for_it(app, client):
    ids = _setup(app, client)
    other = _as(app, OTHER, "Kollegin")
    base = f"/org/{ids['org']}/people/{ids['third']}"
    other.post(base + "/delete", data={"name": "Dritte", "heir": ids["fourth"]})
    r = other.post(base, data={"action": "unblock"}, follow_redirects=True)
    assert "die Löschung zurücknehmen" in r.data.decode()
    assert "Löschung vorgemerkt" in r.data.decode() and "Löschung zurücknehmen" in r.data.decode()
    assert "Löschung am" in other.get(f"/org/{ids['org']}/people").data.decode()
    r = other.post(base + "/delete", data={"action": "undo"}, follow_redirects=True)
    assert "zurückgenommen" in r.data.decode()
    with app.app_context():
        assert db.session.get(OrgDeletion, ids["third"]) is None
        assert db.session.get(UserLogin, ids["third"]).status == UserLogin.ACTIVE


def test_open_points_keep_the_account_and_a_lifted_block_drops_the_mark(app, client):
    ids = _setup(app, client)
    other = _as(app, OTHER, "Kollegin")
    base = f"/org/{ids['org']}/people/{ids['third']}"
    # a running survey can only pass on
    with app.app_context():
        db.session.get(Survey, ids["data"]).status = Survey.RUNNING
        db.session.commit()
    r = other.post(base + "/delete", data={"name": "Dritte", "heir": ids["fourth"], f"item_s{ids['data']}": "delete"})
    assert r.status_code == 302                       # "delete" is no option for a running survey: the preset holds
    with app.app_context():
        assert db.session.get(OrgDeletion, ids["third"]) is not None
        # the heir is blocked meanwhile: at the due time nothing goes, the open point is listed
        accounts.set_blocked(db.session.get(User, ids["fourth"]), True, None)
        db.session.commit()
        assert org_delete.run(utcnow() + timedelta(days=31)) == 0
        row = db.session.get(OrgDeletion, ids["third"])
        assert row.open_points and row.open_points[0]["why"] == "heir"
        assert db.session.get(User, ids["third"]) is not None
    assert "Löschung fällig" in other.get(base).data.decode()
    with app.app_context():
        # a site admin unblocks the account: the mark goes at the next sweep
        accounts.set_blocked(db.session.get(User, ids["third"]), False, None)
        db.session.commit()
        org_delete.run(utcnow() + timedelta(days=31))
        assert db.session.get(OrgDeletion, ids["third"]) is None and db.session.get(User, ids["third"]) is not None


def test_deleting_answers_needs_its_tick(app, client):
    ids = _setup(app, client)
    other = _as(app, OTHER, "Kollegin")
    base = f"/org/{ids['org']}/people/{ids['third']}"
    form = {"name": "Dritte", "heir": ids["fourth"], f"item_s{ids['data']}": "delete"}
    r = other.post(base + "/delete", data=form)
    assert r.status_code == 422 and "nicht bestätigt" in r.data.decode()
    other.post(base + "/delete", data={**form, f"tick_s{ids['data']}": "1", f"item_d{ids['design']}": "delete"})
    with app.app_context():
        org_delete.run(utcnow() + timedelta(days=31))
        assert db.session.get(User, ids["third"]) is None
        assert db.session.get(Survey, ids["data"]) is None and db.session.get(Design, ids["design"]) is None


def test_other_admins_of_the_organisation_are_not_managed_here(app, client):
    ids = _setup(app, client)
    other = _as(app, OTHER, "Kollegin")
    with app.app_context():
        orgs.add(db.session.get(Organisation, ids["org"]), THIRD, role="admin")
        db.session.commit()
    assert other.get(f"/org/{ids['org']}/people/{ids['third']}/delete").status_code == 404
    assert other.get(f"/org/{ids['org']}/people/{ids['third']}").status_code == 404     # nor its type, block, period
    r = other.post(f"/org/{ids['org']}/people/{ids['third']}", data={"action": "account_type", "account_type": ""})
    assert r.status_code == 404
    with app.app_context():
        assert db.session.get(OrgDeletion, ids["third"]) is None


def test_someone_who_also_belongs_elsewhere_is_removed_with_groups_and_home(app, client):
    ids = _setup(app, client)
    # FOURTH also belongs to a second organisation (a group of ME made into one)
    allow(app, FIFTH, keep=True)
    gid2 = _group(client, name="Zweite", members=((FOURTH, "Vierte"),))
    client.post(f"/admin/groups/{gid2}/to-org", data={"name": "Zweite Org", "leads_admin": "1", "keep_group": "1"})
    other = _as(app, OTHER, "Kollegin")
    with app.app_context():
        second = db.session.scalar(db.select(Organisation.id).filter_by(name="Zweite Org"))
        gid = db.session.scalar(db.select(WorkGroup.id).where(WorkGroup.org_id == ids["org"]))
        db.session.add(WorkGroupMember(group_id=gid, orcid=FOURTH, role="member"))
        orgs.set_home(FOURTH, db.session.get(Organisation, ids["org"]))
        db.session.commit()
        orgs.forget()
    # roles beyond the organisation: not managed here any more (audit 2026-10-18, AUTHZ-01), only taken out
    assert other.get(f"/org/{ids['org']}/people/{ids['fourth']}").status_code == 404
    assert other.get(f"/org/{ids['org']}/people/{ids['fourth']}/delete").status_code == 404
    r = other.post(f"/org/{ids['org']}/people", data={"action": "remove", "user_id": ids["fourth"]}, follow_redirects=True)
    text = r.data.decode()
    assert "gehört nicht mehr zur Organisation" in text and "Die Heimat ist jetzt Zweite Org" in text
    with app.app_context():
        orgs.forget()
        assert orgs.home_id_of_orcid(FOURTH) == second
        assert ids["org"] not in orgs.ids_of_orcid(FOURTH)
        assert db.session.scalar(db.select(WorkGroupMember).filter_by(group_id=gid, orcid=FOURTH)) is None
        assert db.session.get(User, ids["fourth"]) is not None


def test_someone_at_home_here_only_is_not_removed(app, client):
    ids = _setup(app, client)
    other = _as(app, OTHER, "Kollegin")
    r = other.post(f"/org/{ids['org']}/people", data={"action": "remove", "user_id": ids["third"]}, follow_redirects=True)
    assert "gehört nur zu dieser Organisation" in r.data.decode()
    with app.app_context():
        orgs.forget()
        assert orgs.home_id_of_orcid(THIRD) == ids["org"]
        assert db.session.scalar(db.select(SurveyMember).filter_by(orcid=THIRD)) is not None


def test_one_account_that_fails_leaves_the_other_marks(app, client, monkeypatch):
    """Gesamtaudit 2026-10-18, FUNC-02: an unexpected error while deleting one marked account ended the sweep; the
    marks after it waited for ever. Now that account waits for the next run and the others go."""
    ids = _setup(app, client)
    with app.app_context():
        for uid in (ids["third"], ids["fourth"]):
            login = db.session.get(UserLogin, uid)
            login.status, login.block_reason = UserLogin.BLOCKED, org_delete.REASON
            db.session.add(OrgDeletion(user_id=uid, org_id=ids["org"], due_at=utcnow() - timedelta(days=1),
                                       choices={}))
        db.session.commit()
    done = []

    def execute(row):
        if row.user_id == ids["third"]:
            raise RuntimeError("a lasting error with this account")
        done.append(row.user_id)
        return True

    monkeypatch.setattr(org_delete, "execute", execute)
    with app.test_request_context():
        assert org_delete.run() == 1
    assert done == [ids["fourth"]]
