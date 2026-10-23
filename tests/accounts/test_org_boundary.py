"""The edge of an organisation for its admins and for the leads of courses (audit 2026-10-18, H-1: AUTHZ-01, AUTH-1,
AUTHZ-02, AUTHZ-03): an organisation admin never takes over, blocks, links, marks for deletion or changes an account
with roles beyond the organisation; an account that exists joins only by accepting an invitation itself (a guest's
home moves only then); a course lead makes no one-time link for an account with roles beyond the course. What was
allowed before (accounts at home here with nothing elsewhere) still works."""
import re

from app import accounts, bulk_invites, orgs
from app.extensions import db
from app.models import OrgDeletion, Organisation, Survey, SurveyMember, User, UserLogin, WorkGroup, WorkGroupMember
from tests.conftest import allow

ADMIN_X = "0000-0002-1825-0097"   # admin of organisation X
ADMIN_Y = "0000-0000-0000-0458"   # admin of organisation Y
PERSON = "0000-0000-0000-0124"    # a researcher at home in "main"
STRANGER = "0000-0000-0000-0239"


def _as(app, orcid, name):
    c = app.test_client()
    c.get(f"/auth/dev-login?orcid={orcid}&name={name}")
    c.post("/terms/", data={"accept": "1"})
    return c


def _orgs(app):
    """Organisations X (admin ADMIN_X, at home there) and Y (admin ADMIN_Y, a guest: home "Gäste")."""
    allow(app, ADMIN_X, ADMIN_Y, PERSON, STRANGER, keep=True)
    clients = {o: _as(app, o, n) for o, n in ((ADMIN_X, "Admin X"), (ADMIN_Y, "Admin Y"), (PERSON, "Person"),
                                                (STRANGER, "Fremd"))}
    with app.app_context():
        x, y = Organisation(name="Org X"), Organisation(name="Org Y")
        db.session.add_all([x, y])
        db.session.flush()
        orgs.add(x, _user(ADMIN_X), role="admin", home=True)
        admin_y = _user(ADMIN_Y)
        orgs.add(y, admin_y, role="admin")
        orgs.set_home(admin_y, orgs.get(orgs.guest_id()))
        db.session.commit()
        orgs.forget()
        assert orgs.is_guest(admin_y) and orgs.is_admin_of(admin_y, y.id)
        ids = {"x": x.id, "y": y.id, "admin_y": admin_y.id, "person": _user(PERSON).id}
    return ids, clients


def _user(orcid):
    return db.session.scalar(db.select(User).filter_by(orcid=orcid))


def _local(app, name, email, home=None, admin_of=None):
    """An account without ORCID, at home in `home`, admin of `admin_of`."""
    with app.app_context():
        user = accounts.create_local(name, email, "", False, None)
        db.session.flush()
        if home is not None:
            orgs.add(db.session.get(Organisation, home), user, home=True)
        if admin_of is not None:
            orgs.add(db.session.get(Organisation, admin_of), user, role="admin")
        db.session.commit()
        orgs.forget()
        return user.id


def _status(app, uid):
    with app.app_context():
        row = db.session.get(UserLogin, uid)
        return row.status if row is not None else UserLogin.ACTIVE


def _invitation(client, org_id):
    page = client.get(f"/org/{org_id}/people").data.decode()
    found = re.search(r"/org/join/([A-Za-z0-9_.-]{20,})", page)
    return found.group(0) if found else None


def _assert_not_managed(app, admin, org_id, uid):
    """None of the account's management reaches it: page, block, user type, survey types, period, one-time link,
    deletion."""
    base = f"/org/{org_id}/people/{uid}"
    assert admin.get(base).status_code == 404
    for form in ({"action": "block"}, {"action": "link"}, {"action": "account_type", "account_type": ""},
                 {"action": "kind", "kind": "survey", "level": "off"}, {"action": "extend", "valid_mode": "none"}):
        assert admin.post(base, data=form).status_code == 404
    assert admin.get(base + "/delete").status_code == 404
    assert admin.post(base + "/delete", data={"name": "x"}).status_code == 404
    assert _status(app, uid) == UserLogin.ACTIVE
    with app.app_context():
        assert db.session.get(OrgDeletion, uid) is None


def test_a_guest_admin_of_another_organisation_is_neither_taken_in_nor_managed(app, client):
    """AUTHZ-01: taking in a guest by its iD moves no home; the admin of Y (a guest) is never blocked or deleted by
    the admin of X, not even after joining X by its own consent."""
    ids, c = _orgs(app)
    admin_x = c[ADMIN_X]
    r = admin_x.post(f"/org/{ids['x']}/people", data={"action": "add", "orcid": ADMIN_Y})
    assert r.status_code == 302
    with app.app_context():
        orgs.forget()
        user = db.session.get(User, ids["admin_y"])
        assert orgs.is_guest(user) and ids["x"] not in orgs.ids_of(user)        # nothing moved without consent
    link = _invitation(admin_x, ids["x"])
    assert link is not None
    _assert_not_managed(app, admin_x, ids["x"], ids["admin_y"])
    # the person accepts: member and, as a guest, at home in X; still an admin of Y, so X does not manage the account
    page = c[ADMIN_Y].get(link).data.decode()
    assert "Beitreten" in page and "Heimat" in page
    c[ADMIN_Y].post(link, data={"answer": "join"})
    with app.app_context():
        orgs.forget()
        user = db.session.get(User, ids["admin_y"])
        assert orgs.home_id(user) == ids["x"] and orgs.is_admin_of(user, ids["y"])
        assert not orgs.manages(_user(ADMIN_X), user, db.session.get(Organisation, ids["x"]))
    _assert_not_managed(app, admin_x, ids["x"], ids["admin_y"])
    assert c[ADMIN_Y].get(f"/org/{ids['y']}/people").status_code == 200


def test_an_account_of_another_organisation_joins_only_by_accepting(app, client):
    """AUTHZ-02: an account at home in "main" is not made a member by its iD; the link is for its own account only;
    declining leaves everything as it was."""
    ids, c = _orgs(app)
    admin_x = c[ADMIN_X]
    admin_x.post(f"/org/{ids['x']}/people", data={"action": "add", "orcid": PERSON})
    with app.app_context():
        orgs.forget()
        assert ids["x"] not in orgs.ids_of(db.session.get(User, ids["person"]))
    link = _invitation(admin_x, ids["x"])
    assert link is not None and _invitation(admin_x, ids["x"]) is None                  # shown once
    c[STRANGER].post(link, data={"answer": "join"})                                     # someone else's link
    c[PERSON].post(link, data={"answer": "no"})
    with app.app_context():
        orgs.forget()
        assert ids["x"] not in orgs.ids_of(_user(STRANGER))
        assert ids["x"] not in orgs.ids_of(db.session.get(User, ids["person"]))
    c[PERSON].post(link, data={"answer": "join"})
    with app.app_context():
        orgs.forget()
        person = db.session.get(User, ids["person"])
        assert ids["x"] in orgs.ids_of(person) and orgs.home_id(person) == orgs.default_id()  # no guest: home stays
    # an invitation of someone no longer an admin of X is void
    admin_x.post(f"/org/{ids['x']}/people", data={"action": "add", "orcid": STRANGER})
    link = _invitation(admin_x, ids["x"])
    with app.app_context():
        orgs.row_of(ids["x"], _user(ADMIN_X).id).role = "member"
        db.session.commit()
    c[STRANGER].post(link, data={"answer": "join"})
    with app.app_context():
        orgs.forget()
        assert ids["x"] not in orgs.ids_of(_user(STRANGER))


def test_no_one_time_link_for_a_local_account_that_is_admin_elsewhere(app, client):
    """AUTH-1: an account without ORCID at home in X and admin of Y is not taken over by X's one-time link; nor
    blocked, changed or marked for deletion."""
    ids, c = _orgs(app)
    target = _local(app, "Ziel", "ziel@example.org", home=ids["x"], admin_of=ids["y"])
    with app.app_context():
        assert not orgs.manages(_user(ADMIN_X), db.session.get(User, target), db.session.get(Organisation, ids["x"]))
    _assert_not_managed(app, c[ADMIN_X], ids["x"], target)
    page = c[ADMIN_X].get(f"/org/{ids['x']}/people").data.decode()
    assert f"/org/{ids['x']}/people/{target}\"" not in page                          # no "Verwalten" in the list


def test_the_own_accounts_stay_managed_and_roles_elsewhere_end_it(app, client, survey):
    """What was allowed stays: an account at home in X with nothing elsewhere is managed (one-time link, block,
    deletion page). A role in a survey of another organisation, or a group there, ends it; taking out still works."""
    ids, c = _orgs(app)
    admin_x = c[ADMIN_X]
    own = _local(app, "Eigen", "eigen@example.org", home=ids["x"])
    base = f"/org/{ids['x']}/people/{own}"
    assert admin_x.get(base).status_code == 200
    r = admin_x.post(base, data={"action": "link"})
    assert r.status_code == 200 and "/auth/password/" in r.data.decode()
    admin_x.post(base, data={"action": "block"})
    assert _status(app, own) == UserLogin.BLOCKED
    admin_x.post(base, data={"action": "unblock"})
    assert admin_x.get(base + "/delete").status_code == 200
    # a survey of organisation Y with the account in it
    with app.app_context():
        db.session.get(Survey, survey).org_id = ids["y"]
        db.session.add(SurveyMember(survey_id=survey, user_id=own, name="Eigen", role="viewer"))
        db.session.commit()
        orgs.forget()
        assert orgs.outside_roles(db.session.get(User, own), db.session.get(Organisation, ids["x"])) == {"survey"}
    _assert_not_managed(app, admin_x, ids["x"], own)
    # a group of organisation Y instead
    with app.app_context():
        db.session.execute(db.delete(SurveyMember).where(SurveyMember.user_id == own))
        group = WorkGroup(name="Y-Gruppe", org_id=ids["y"])
        group.members.append(WorkGroupMember(user_id=own, name="Eigen", role="member"))
        db.session.add(group)
        db.session.commit()
        orgs.forget()
    _assert_not_managed(app, admin_x, ids["x"], own)
    # it belongs to Y now too: X may still take it out (its home moves to Y)
    r = admin_x.post(f"/org/{ids['x']}/people", data={"action": "remove", "user_id": own})
    assert r.status_code == 302
    with app.app_context():
        orgs.forget()
        assert orgs.home_id(db.session.get(User, own)) == ids["y"]


def test_an_id_named_elsewhere_before_its_first_sign_in_is_not_given_a_home(app, client):
    """A pending account already in a group of another organisation is not put on X's sign-in list."""
    ids, c = _orgs(app)
    new = "0000-0000-0000-0343"
    with app.app_context():
        from app import people_keys

        group = WorkGroup(name="Y-Gruppe", org_id=ids["y"])
        group.members.append(WorkGroupMember(user=people_keys.account(new, "Neu"), name="Neu", role="member"))
        db.session.add(group)
        db.session.commit()
    r = c[ADMIN_X].post(f"/org/{ids['x']}/people", data={"action": "add", "orcid": new})
    assert r.status_code == 422 and "Rollen in einer anderen Organisation" in r.data.decode()


def _course_with_student(app, client):
    from tests.accounts.test_account_types import _course

    gid = _course(app, client)
    with app.app_context():
        student = accounts.create_local("Studi", "studi@example.org", "", False, None)
        db.session.flush()
        db.session.get(UserLogin, student.id).origin_group_id = gid
        db.session.add(WorkGroupMember(group_id=gid, user_id=student.id, name="Studi", role="member"))
        db.session.commit()
        return gid, student.id


def test_a_course_lead_makes_no_link_for_an_account_with_roles_beyond_the_course(app, client, survey):
    """AUTHZ-03: the lead's one-time link only for accounts whose roles lie within the course: a survey among the
    course's people is fine, a survey with someone outside the course or another group is not."""
    gid, student = _course_with_student(app, client)
    assert client.post(f"/course/{gid}/link/{student}").status_code == 200
    with app.app_context():  # survey 1 of the lead (a member of the course) with the student in it
        db.session.add(SurveyMember(survey_id=survey, user_id=student, name="Studi", role="editor"))
        db.session.commit()
    assert client.post(f"/course/{gid}/link/{student}").status_code == 200           # within the course
    allow(app, STRANGER, keep=True)
    _as(app, STRANGER, "Fremd")
    with app.app_context():  # someone outside the course in the same survey: the link would reach their survey
        db.session.add(SurveyMember(survey_id=survey, user_id=_user(STRANGER).id, name="Fremd", role="admin"))
        db.session.commit()
        assert bulk_invites.beyond_course(db.session.get(User, student), db.session.get(WorkGroup, gid))
    assert client.post(f"/course/{gid}/link/{student}").status_code == 404
    assert f"/course/{gid}/link/{student}" not in client.get(f"/groups/{gid}").data.decode()
    with app.app_context():
        db.session.execute(db.delete(SurveyMember).where(SurveyMember.user_id == _user(STRANGER).id))
        other = WorkGroup(name="Andere Gruppe")
        other.members.append(WorkGroupMember(user_id=student, name="Studi", role="member"))
        db.session.add(other)
        db.session.commit()
    assert client.post(f"/course/{gid}/link/{student}").status_code == 404


def test_a_type_changed_by_the_organisation_stops_short_of_accounts_with_roles_elsewhere(app, client):
    """Follow-up of AUTHZ-01: the organisation's admins change one of its user types. "Carry over" reaches only the
    accounts they manage, and the deletion period (read when the sweep runs) deletes no account with roles beyond
    the organisation; the type's page says how many are left out. The own accounts are changed and deleted as
    before."""
    from datetime import timedelta

    from app import account_types, kind_access
    from app.models import AccountType, utcnow

    ids, c = _orgs(app)
    own = _local(app, "Eigen", "eigen@example.org", home=ids["x"])
    foreign = _local(app, "Fremdadmin", "fremdadmin@example.org", home=ids["x"], admin_of=ids["y"])
    with app.app_context():
        t = AccountType(org_id=ids["x"], name_de="Praktikum", name_en="Internship", duration="",
                        kinds={"survey": "create", "live": "create", "interview": "create", "diary": "create"})
        db.session.add(t)
        db.session.flush()
        for uid in (own, foreign):
            account_types.assign(db.session.get(User, uid), t, "", log=False)
            kind_access.store(db.session.get(User, uid), t.kinds)
        db.session.commit()
        tid = t.id
    admin_x = c[ADMIN_X]
    page = admin_x.get(f"/org/{ids['x']}/types/{tid}").data.decode()
    assert 'id="ausgenommen"' in page and "1 Konten dieses Typs sind ausgenommen" in page
    # the organisation takes "create" away and carries it over
    admin_x.post(f"/org/{ids['x']}/types", data={"action": "save", "type_id": tid, "name_de": "Praktikum",
                                                  "kind_survey": "view", "kind_live": "view", "kind_interview": "view",
                                                  "kind_diary": "view", "duration_mode": "none", "delete_months": "1"})
    r = admin_x.post(f"/org/{ids['x']}/types", data={"action": "apply_all", "type_id": tid}, follow_redirects=True)
    assert "auf 1 Konten übertragen" in r.data.decode() and "ausgenommen" in r.data.decode()
    with app.app_context():
        assert kind_access.full(kind_access.stored(db.session.get(User, own)))["survey"] == "view"
        assert kind_access.full(kind_access.stored(db.session.get(User, foreign)))["survey"] == "create"
        # both ended long ago: the sweep deletes the own account, the other one waits for a site admin
        for uid in (own, foreign):
            row = db.session.get(UserLogin, uid)
            row.valid_until = utcnow() - timedelta(days=90)
            row.status, row.block_reason = UserLogin.BLOCKED, account_types.EXPIRED
        db.session.commit()
        account_types.delete_due()
        assert db.session.get(User, own) is None
        assert db.session.get(User, foreign) is not None
        assert account_types.deletion_due(db.session.get(UserLogin, foreign))     # listed for a site admin
    # a site admin's carrying over leaves out nobody
    with app.app_context():
        me = db.session.scalar(db.select(User).filter_by(is_admin=True))
        assert account_types.exempt_of(db.session.get(AccountType, tid), me) == []


def _y_shares_with(app, uid, kind):
    """Admin Y shares a design, an AInstein project or a linked element of organisation Y with the account `uid`."""
    from app import theme
    from app.models import Construct, ConstructShare, Design, DesignEditor, DesignShare, ResearchProject

    with app.app_context():
        y_org = db.session.scalar(db.select(Organisation.id).filter_by(name="Org Y"))
        owner = _user(ADMIN_Y).id
        if kind in ("design", "design_edit"):
            design = Design(owner_id=owner, name="Y-Design", data=theme.standard(), org_id=y_org)
            design.shares.append(DesignShare(user_id=uid)) if kind == "design" else None
            db.session.add(design)
            if kind == "design_edit":
                db.session.flush()
                db.session.add(DesignEditor(design_id=design.id, user_id=uid))
        elif kind == "project":
            db.session.add(ResearchProject(user_id=owner, org_id=y_org, name="Y-Projekt", idea="x",
                                           shares=[{"kind": "person", "user_id": uid, "name": "Lokal", "role": "edit"}]))
        else:
            element = Construct(key=f"y_{uid}", name="Y-Element", visibility="private", created_by_id=owner, org_id=y_org)
            db.session.add(element)
            db.session.flush()
            db.session.add(ConstructShare(construct_id=element.id, user_id=uid))
        db.session.commit()


def test_personal_shares_of_another_organisation_end_the_management(app, client):
    """Audit 2026-10-22, B-01: a design, AInstein project or linked element of organisation Y shared with an account
    at home in X did not count; X's admin made a one-time link, set the password and worked in Y's project."""
    for n, kind in enumerate(("design", "design_edit", "project", "element")):
        if n == 0:
            ids, c = _orgs(app)
        uid = _local(app, f"Lokal {n}", f"lokal{n}@example.org", home=ids["x"])
        base = f"/org/{ids['x']}/people/{uid}"
        _y_shares_with(app, uid, kind)
        with app.app_context():
            target, x = db.session.get(User, uid), orgs.get(ids["x"])
            assert orgs.outside_roles(target, x) == {"share"}, kind
            assert not orgs.manages(_user(ADMIN_X), target, x), kind
        r = c[ADMIN_X].post(base, data={"action": "link"})
        assert "/auth/password/" not in r.data.decode(), kind


def test_a_course_lead_makes_no_link_for_an_account_with_shares_from_outside(app, client):
    """B-01 for the leads of courses (bulk_invites.beyond_course): a share by someone outside the course."""
    gid, student = _course_with_student(app, client)
    _orgs(app)
    _y_shares_with(app, student, "project")
    with app.app_context():
        assert bulk_invites.beyond_course(db.session.get(User, student), db.session.get(WorkGroup, gid))
    assert client.post(f"/course/{gid}/link/{student}").status_code == 404
