"""User types with a period (app/account_types.py, konzept-nutzertypen-2026-10-15.md, Ralf 2026-10-15 "alle wie
empfohlen"): "Mitarbeitende" seeded, the other four as templates per organisation (Ralf 2026-10-16), type and period chosen in "Person hinzufügen" and started with the first sign-in, a
block with the reason "expired" at the end (at once, before the sweep), "verlängern", the supervisor as admin of new
surveys, deletion after the type's months only when nothing has to be handed over, courses as groups."""
from datetime import timedelta

from app.extensions import db
from app.models import AccountType, SignInOrcid, Survey, User, UserLogin, WorkGroup, WorkGroupMember, utcnow
from tests.conftest import allow

ADMIN = "0000-0000-0000-001X"
OTHER = "0000-0002-1825-0097"


def _as(client, orcid, name="Studentin"):
    client.get(f"/auth/dev-login?orcid={orcid}&name={name}")
    client.post("/terms/", data={"accept": "1"})
    return client


def _type(app, key):
    """The type `key` of the start organisation; a template is added the first time (Ralf 2026-10-16: each
    organisation starts with "Mitarbeitende" only)."""
    with app.app_context():
        from app import account_types

        if key == "staff":
            return db.session.scalar(db.select(AccountType.id).filter_by(key=key))
        name = account_types.template(key)["name_de"]
        found = db.session.scalar(db.select(AccountType.id).filter_by(name_de=name, org_id=None))
        if found is None:
            found = account_types.from_template(key, None).id
            db.session.commit()
        return found


def _login(app, orcid=OTHER):
    with app.app_context():
        row = db.session.scalar(db.select(UserLogin).join(User, User.id == UserLogin.user_id).filter(User.orcid == orcid))
        db.session.expunge_all()
        return row


def _uid(app, orcid=OTHER):
    with app.app_context():
        return db.session.scalar(db.select(User.id).filter_by(orcid=orcid))


def test_only_staff_is_seeded_and_the_rest_are_templates(app):
    with app.app_context():
        from app import account_types

        assert [t.key for t in account_types.all_types()] == ["staff"]
        assert [t["key"] for t in account_types.TEMPLATES] == ["bachelor", "master", "internship", "course"]
        assert account_types.default_type().key == "staff"
        assert account_types.describe("m:4") == "4 Monate" and account_types.describe("") == "unbefristet"


def test_end_of_a_period_in_german_time():
    from datetime import datetime

    from app import account_types

    start = datetime(2026, 10, 15, 12, 0)  # UTC
    # four months from 15 Oct: 15 Feb, 23:59 in Berlin (winter, UTC+1) = 22:59 UTC
    assert account_types.end_for("m:4", start) == datetime(2027, 2, 15, 22, 59)
    assert account_types.end_for("w:2", start) == datetime(2026, 10, 29, 22, 59)
    assert account_types.end_for("", start) is None
    assert account_types.needs_input("w:") and not account_types.needs_input("w:6")


def test_listed_orcid_takes_type_and_period_at_first_sign_in(app, client):
    bachelor = _type(app, "bachelor")
    r = client.post("/admin/admit", data={"action": "add", "orcid": OTHER, "account_type": bachelor,
                                                  "valid_mode": "type"})
    assert r.status_code in (302, 303)
    with app.app_context():
        assert db.session.get(SignInOrcid, OTHER).account_type_id == bachelor
    _as(app.test_client(), OTHER)
    row = _login(app)
    assert row.account_type_id == bachelor and row.valid_from is not None
    assert timedelta(days=118) < row.valid_until - row.valid_from < timedelta(days=125)
    assert row.kinds == {"survey": "create", "live": "view", "interview": "view", "diary": "view"}  # "Anlegen" from the type
    assert row.supervisor_id == _uid(app, ADMIN)  # whoever added the iD supervises


def test_internship_needs_its_number_of_weeks(app, client):
    internship = _type(app, "internship")
    r = client.post("/admin/accounts/new", data={"name": "Paul Praktikant", "email": "paul@example.org",
                                                  "account_type": internship, "valid_mode": "type"})
    assert r.status_code == 422 and "Wochen" in r.data.decode()
    r = client.post("/admin/accounts/new", data={"name": "Paul Praktikant", "email": "paul@example.org",
                                                  "account_type": internship, "valid_mode": "w", "valid_n": "6"})
    assert r.status_code in (302, 303)
    with app.app_context():
        uid = db.session.scalar(db.select(UserLogin.user_id).filter_by(email="paul@example.org"))
        row = db.session.get(UserLogin, uid)
        assert row.valid_spec == "w:6" and row.valid_from is None and row.valid_until is None  # starts with the password
        from app import account_types

        account_types.start_pending(db.session.get(User, uid))
        assert row.valid_until is not None and row.valid_spec is None


def _ended(app, orcid=OTHER, days=1):
    master = _type(app, "master")
    with app.app_context():
        row = db.session.scalar(db.select(UserLogin).join(User, User.id == UserLogin.user_id).filter(User.orcid == orcid))
        row.valid_until = utcnow() - timedelta(days=days)
        row.account_type_id = master
        db.session.commit()


def test_end_blocks_at_once_and_names_whom_to_ask(app, client):
    allow(app, OTHER)
    guest = _as(app.test_client(), OTHER)
    assert guest.get("/dashboard/").status_code == 200
    _ended(app)
    # signed out at the next request, before the hourly sweep marked anything
    assert guest.get("/dashboard/").status_code in (302, 401)
    again = app.test_client().get(f"/auth/dev-login?orcid={OTHER}&name=Studentin")
    assert again.status_code == 403
    with app.app_context():
        from app import account_types

        assert account_types.expire_due() == 1
        row = db.session.scalar(db.select(UserLogin).filter_by(user_id=_uid(app)))
        assert row.status == UserLogin.BLOCKED and row.block_reason == "expired"
        with app.test_request_context():
            assert "abgelaufen" in account_types.ended_said(db.session.get(User, _uid(app)))


def test_unblock_asks_for_an_extension_and_extending_opens(app, client):
    allow(app, OTHER)
    _as(app.test_client(), OTHER)
    _ended(app)
    with app.app_context():
        from app import account_types

        account_types.expire_due()
    uid = _uid(app)
    r = client.post(f"/admin/accounts/{uid}/lock", data={"action": "unblock"}, follow_redirects=True)
    assert "verlängern" in r.data.decode()
    assert _login(app).status == UserLogin.BLOCKED
    client.post(f"/admin/accounts/{uid}", data={"action": "extend", "valid_mode": "m", "valid_n": "2"})
    row = _login(app)
    assert row.status == UserLogin.ACTIVE and row.block_reason is None and row.valid_until > utcnow() + timedelta(days=50)
    assert _as(app.test_client(), OTHER).get("/dashboard/").status_code == 200


def test_an_admins_block_stays_when_extended(app, client):
    allow(app, OTHER)
    _as(app.test_client(), OTHER)
    uid = _uid(app)
    client.post(f"/admin/accounts/{uid}/lock", data={"action": "block"})
    client.post(f"/admin/accounts/{uid}", data={"action": "extend", "valid_mode": "w", "valid_n": "3"})
    row = _login(app)
    assert row.status == UserLogin.BLOCKED and row.block_reason is None


def test_supervisor_becomes_admin_of_new_surveys(app, client):
    bachelor = _type(app, "bachelor")
    client.post("/admin/admit", data={"action": "add", "orcid": OTHER, "account_type": bachelor,
                                              "valid_mode": "type"})
    student = _as(app.test_client(), OTHER)
    student.post("/surveys/new", data={"title": "Abschlussarbeit", "languages": ["de"], "default_language": "de"})
    with app.app_context():
        s = db.session.scalar(db.select(Survey).filter_by(title="Abschlussarbeit"))
        roles = {m.orcid: m.role for m in s.members}
    assert roles == {OTHER: "admin", ADMIN: "admin"}
    assert "Betreuung" in student.get("/account/").data.decode()


def test_deletion_after_the_period_only_without_open_points(app, client):
    allow(app, OTHER)
    student = _as(app.test_client(), OTHER)
    student.post("/surveys/new", data={"title": "Entwurf", "languages": ["de"], "default_language": "de"})
    _ended(app, days=400)
    with app.app_context():
        from app import account_types

        account_types.expire_due()
        assert account_types.delete_due() == 1  # only a draft: it goes with the account
        assert db.session.scalar(db.select(User).filter_by(orcid=OTHER)) is None
        assert db.session.scalar(db.select(Survey).filter_by(title="Entwurf")) is None


def test_deletion_waits_while_a_running_survey_belongs_to_nobody_else(app, client):
    allow(app, OTHER)
    student = _as(app.test_client(), OTHER)
    student.post("/surveys/new", data={"title": "Feld", "languages": ["de"], "default_language": "de"})
    _ended(app, days=400)
    with app.app_context():
        s = db.session.scalar(db.select(Survey).filter_by(title="Feld"))
        s.status = Survey.RUNNING
        db.session.commit()
        from app import account_types

        account_types.expire_due()
        assert account_types.delete_due() == 0
    page = client.get("/admin/people?f_end=due").data.decode()
    assert "Studentin" in page


def test_course_group_gives_type_and_end_to_new_members(app, client):
    course = _type(app, "course")
    with app.app_context():
        group = WorkGroup(name="Methoden I")
        db.session.add(group)
        db.session.commit()
        gid = group.id
    end = (utcnow() + timedelta(days=100)).date().isoformat()
    client.post(f"/admin/groups/{gid}", data={"action": "course", "account_type": course, "course_until": end})
    with app.app_context():
        group = db.session.get(WorkGroup, gid)
        assert group.account_type_id == course and group.course_until is not None
        group.members.append(WorkGroupMember(orcid=OTHER, name="Studentin", role="member"))
        db.session.commit()
    _as(app.test_client(), OTHER)
    row = _login(app)
    assert row.account_type_id == course and row.origin_group_id == gid
    assert row.valid_until.date().isoformat() in (end, (utcnow() + timedelta(days=99)).date().isoformat())
    assert row.kinds == {"survey": "create", "live": "create", "interview": "off", "diary": "off"}


def test_existing_staff_joining_a_course_keeps_its_type(app, client):
    allow(app, OTHER)
    _as(app.test_client(), OTHER)
    staff = _login(app)
    assert staff.valid_until is None
    with app.app_context():
        from app import account_types

        group = WorkGroup(name="Methoden II", account_type_id=_type(app, "course"),
                          course_until=utcnow() + timedelta(days=30))
        db.session.add(group)
        db.session.commit()
        assert not account_types.reopen_for_course(db.session.get(User, _uid(app)), group)
    assert _login(app).valid_until is None


def test_banner_before_the_end_and_got_it(app, client):
    allow(app, OTHER)
    guest = _as(app.test_client(), OTHER)
    with app.app_context():
        row = db.session.get(UserLogin, _uid(app))
        row.valid_until = utcnow() + timedelta(days=10)
        db.session.commit()
    page = guest.get("/dashboard/").data.decode()
    assert "Ihr Zugang endet am" in page
    guest.post("/account/expiry-seen", data={"step": "14", "next": "/dashboard/"})
    assert "Ihr Zugang endet am" not in guest.get("/dashboard/").data.decode()


def test_types_live_in_the_management_of_each_organisation(app, client):
    """Ralf 2026-10-16: types per organisation, each starts with "Mitarbeitende" only; the rest from templates."""
    with app.app_context():
        from app import orgs

        main = orgs.default_id()
    # only under "Organisationen", no longer under "Personen" too (UI/UX audit 2026-10-16, 21)
    assert 'id="nutzertypen"' not in client.get("/admin/people").data.decode()
    assert f'href="/org/{main}/types"' in client.get(f"/admin/orgs/{main}").data.decode()  # the organisation's page
    page = client.get(f"/org/{main}/types").data.decode()
    assert "Mitarbeitende" in page and "Bachelorarbeit" in page  # the template as a button
    r = client.post(f"/org/{main}/types", data={"action": "template", "template": "bachelor"})
    assert r.status_code in (302, 303)
    assert "Aus Vorlage" in client.get(f"/org/{main}/types").data.decode()
    r = client.post(f"/org/{main}/types", data={"action": "new", "name_de": "Gast", "name_en": "Guest",
                                                "duration_mode": "w", "duration_n": "2"})
    assert r.status_code in (302, 303)
    with app.app_context():
        assert db.session.scalar(db.select(AccountType.duration).filter_by(name_de="Gast")) == "w:2"
        assert db.session.scalar(db.select(AccountType.org_id).filter_by(name_de="Bachelorarbeit")) is None


def _course(app, client, lead=ADMIN):
    course = _type(app, "course")
    with app.app_context():
        lead_user = db.session.scalar(db.select(User).filter_by(orcid=lead))
        group = WorkGroup(name="Statistik", may_create=True)
        group.members.append(WorkGroupMember(orcid=lead, user_id=lead_user.id, name="Leitung", role="lead"))
        db.session.add(group)
        db.session.commit()
        gid = group.id
    end = (utcnow() + timedelta(days=60)).date().isoformat()
    client.post(f"/admin/groups/{gid}", data={"action": "course", "account_type": course, "course_until": end})
    return gid


def test_address_list_without_mail_gives_links_as_a_file(app, client):
    gid = _course(app, client)
    allow(app, OTHER)
    _as(app.test_client(), OTHER)
    with app.app_context():
        row = db.session.get(UserLogin, _uid(app))
        row.email = "da@example.org"
        db.session.commit()
    text = "neu1@example.org; neu2@example.org\nkaputt@\nneu1@example.org, da@example.org"
    preview = client.post(f"/course/{gid}/list", data={"addresses": text, "step": "preview"}).data.decode()
    assert "neu1@example.org" in preview and "Einladen und Links herunterladen" in preview
    r = client.post(f"/course/{gid}/list", data={"addresses": text, "step": "send", "days": "30"})
    assert r.headers["Content-Type"].startswith("text/csv")
    body = r.data.decode("utf-8-sig")
    assert body.count("/invite/") == 2 and "neu2@example.org" in body
    with app.app_context():
        group = db.session.get(WorkGroup, gid)
        assert any(m.orcid == OTHER for m in group.members)  # an account of the list: a member at once
    # the link works without mail: a new account with the course's type and end
    link = body.splitlines()[1].split(";")[1]
    path = link[link.index("/invite/"):]
    guest = app.test_client()
    assert guest.get(path).status_code == 200
    guest.post(path, data={"way": "password", "name": "Neu Eins", "password": "lange genug passphrase 2026",
                           "repeat": "lange genug passphrase 2026"})
    with app.app_context():
        row = db.session.scalar(db.select(UserLogin).filter_by(email="neu1@example.org"))
        assert row is not None and row.account_type_id == _type(app, "course") and row.origin_group_id == gid
        assert row.valid_until is not None


def test_course_join_link_without_mail_creates_a_password_account(app, client):
    gid = _course(app, client)
    r = client.post(f"/join/group/{gid}", data={"days": "14", "max_uses": "30"}, follow_redirects=True)
    page = r.data.decode()
    import re

    token = re.search(r"/join/([A-Za-z0-9_-]{30,})", page).group(1)
    guest = app.test_client()
    assert "Konto anlegen und beitreten" in guest.get(f"/join/{token}").data.decode()
    guest.post(f"/join/{token}", data={"way": "password", "name": "Studi", "email": "studi@example.org",
                                       "password": "lange genug passphrase 2026", "repeat": "lange genug passphrase 2026"})
    with app.app_context():
        row = db.session.scalar(db.select(UserLogin).filter_by(email="studi@example.org"))
        assert row is not None and row.account_type_id == _type(app, "course")
        uid = row.user_id
    lead_page = client.get(f"/groups/{gid}").data.decode()
    assert "Einmal-Link" in lead_page and 'id="lehrveranstaltung"' in lead_page
    r = client.post(f"/course/{gid}/link/{uid}")
    assert r.status_code == 200 and "/auth/password/" in r.data.decode()


def test_the_course_join_link_does_not_say_which_address_has_an_account(app, client):
    """Audit 2026-10-18, AUTH-5: "Mit dieser Adresse gibt es schon ein Konto" told whoever held the link whether an
    address had an account. Now the password is checked first, a taken address gets a reply that names no account,
    and each such try counts against the link tries of the sender (limits.allow_invite_try)."""
    gid = _course(app, client)
    page = client.post(f"/join/group/{gid}", data={"days": "14", "max_uses": "30"}, follow_redirects=True).data.decode()
    import re

    token = re.search(r"/join/([A-Za-z0-9_-]{30,})", page).group(1)
    good = "lange genug passphrase 2026"
    app.test_client().post(f"/join/{token}", data={"way": "password", "name": "Studi", "email": "studi@example.org",
                                                   "password": good, "repeat": good})
    probe = app.test_client()
    weak = probe.post(f"/join/{token}", data={"way": "password", "name": "Probe", "email": "studi@example.org",
                                              "password": "kurz", "repeat": "kurz"}, follow_redirects=True).data.decode()
    assert "kein neues Konto" not in weak and "schon ein Konto" not in weak  # the password's problem comes first
    addr = {"REMOTE_ADDR": "198.51.100.7"}  # a sender the limits count (not the loopback of the tests)
    taken = probe.post(f"/join/{token}", data={"way": "password", "name": "Probe", "email": "studi@example.org",
                                               "password": good, "repeat": good}, environ_base=addr,
                       follow_redirects=True).data.decode()
    assert "kein neues Konto" in taken and "schon ein Konto mit" not in taken and "gibt es schon" not in taken
    app.config["TOKEN_TRY_LIMIT"] = 1
    assert probe.get(f"/join/{token}", environ_base=addr).status_code == 429  # the try counted


def test_a_limited_account_brings_in_nobody_new(app, client):
    bachelor = _type(app, "bachelor")
    client.post("/admin/admit", data={"action": "add", "orcid": OTHER, "account_type": bachelor, "valid_mode": "type"})
    _as(app.test_client(), OTHER)
    with app.app_context():
        from app import auth

        user = db.session.scalar(db.select(User).filter_by(orcid=OTHER))
        assert auth.may_create_surveys(user)
        assert not auth.may_admit(user)                       # no invitation of new people, no join link
        assert auth.may_admit(user, ADMIN)                    # people who can sign in already


def test_supervisors_are_added_by_name_and_removed_with_the_x(app, client):
    """Betreuung picked by name from the people of the home organisation (Ralf 2026-10-18), several of them, shown as a
    list with × instead of a colour in a list box; a click adds one, the × takes one out."""
    from app import account_types

    third, fourth = "0000-0000-0000-0124", "0000-0000-0000-0239"
    allow(app, OTHER, third, fourth)
    _as(app.test_client(), OTHER)
    _as(app.test_client(), third, "Betreuerin")
    _as(app.test_client(), fourth, "Zweitbetreuer")
    uid, tid, fid = _uid(app), _uid(app, third), _uid(app, fourth)
    page = client.get(f"/admin/accounts/{uid}").data.decode()
    form = page[page.index('<select id="supervisor"'):]
    form = form[:form.index("</select>")]
    assert f'<option value="{tid}">Betreuerin</option>' in form and f'value="{uid}"' not in form   # not oneself
    assert 'data-submit-on-change' in form and "noch niemand" in page
    client.post(f"/admin/accounts/{uid}", data={"action": "supervisor", "supervisor": str(tid)})
    client.post(f"/admin/accounts/{uid}", data={"action": "supervisor", "supervisor": str(fid)})
    with app.app_context():
        user = db.session.get(User, uid)
        assert [u.id for u in account_types.supervisors_of(user)] == [tid, fid]
    assert _login(app).supervisor_id == tid                                   # the first, for the code before (0081)
    page = client.get(f"/admin/accounts/{uid}").data.decode()
    listed = page[page.index('class="supervisor-list"'):page.index('id="supervisor"')]
    assert "Betreuerin" in listed and "Zweitbetreuer" in listed and 'value="supervisor_remove"' in listed
    assert f'<option value="{tid}">' not in page                              # chosen ones leave the list to add
    page = client.post(f"/admin/accounts/{uid}", data={"action": "supervisor", "supervisor": str(uid)},
                       follow_redirects=True).data.decode()
    assert "kann das Konto nicht betreuen" in page
    client.post(f"/admin/accounts/{uid}", data={"action": "supervisor_remove", "supervisor": str(tid)})
    with app.app_context():
        assert [u.id for u in account_types.supervisors_of(db.session.get(User, uid))] == [fid]
    assert _login(app).supervisor_id == fid


def test_every_supervisor_becomes_admin_and_they_go_with_a_type_without_supervision(app, client):
    """All supervisors become admins of a new survey; a type without supervision (staff) takes them all away, and they
    stay admins of the surveys they are in already (Ralf 2026-10-18)."""
    from app import account_types

    third = "0000-0000-0000-0124"
    bachelor = _type(app, "bachelor")
    allow(app, third)
    _as(app.test_client(), third, "Betreuerin")
    client.post("/admin/admit", data={"action": "add", "orcid": OTHER, "account_type": bachelor, "valid_mode": "type"})
    student = _as(app.test_client(), OTHER)
    uid, tid = _uid(app), _uid(app, third)
    client.post(f"/admin/accounts/{uid}", data={"action": "supervisor", "supervisor": str(tid)})
    student.post("/surveys/new", data={"title": "Abschlussarbeit", "languages": ["de"], "default_language": "de"})
    with app.app_context():
        s = db.session.scalar(db.select(Survey).filter_by(title="Abschlussarbeit"))
        assert {m.orcid: m.role for m in s.members} == {OTHER: "admin", ADMIN: "admin", third: "admin"}
        staff = next(t for t in account_types.all_types() if not t.supervised)
        sid = staff.id
    client.post(f"/admin/accounts/{uid}", data={"action": "account_type", "account_type": str(sid)})
    with app.app_context():
        assert account_types.supervisors_of(db.session.get(User, uid)) == []
        s = db.session.scalar(db.select(Survey).filter_by(title="Abschlussarbeit"))
        assert {m.orcid for m in s.members if m.role == "admin"} >= {ADMIN, third}   # nothing taken from surveys
    assert _login(app).supervisor_id is None


def test_deleting_a_supervisor_removes_the_supervision(app, client):
    from app import accounts, account_types

    third = "0000-0000-0000-0124"
    allow(app, OTHER, third)
    _as(app.test_client(), OTHER)
    _as(app.test_client(), third, "Betreuerin")
    uid, tid = _uid(app), _uid(app, third)
    client.post(f"/admin/accounts/{uid}", data={"action": "supervisor", "supervisor": str(tid)})
    with app.app_context():
        me = db.session.scalar(db.select(User).filter_by(orcid=ADMIN))
        accounts.delete(db.session.get(User, tid), "admin", {}, admin=me)
        db.session.commit()
        assert account_types.supervisors_of(db.session.get(User, uid)) == []


def test_many_people_bring_a_search_field_outside_the_form(app, client):
    """More than ten people: a search field over the list (Ralf 2026-10-18), outside the form, so typing a name sends
    nothing; a click in the list adds."""
    allow(app, OTHER)
    _as(app.test_client(), OTHER)
    page = client.get(f"/admin/accounts/{_uid(app)}").data.decode()
    assert 'data-filter-select="supervisor"' not in page                 # a short list needs none
    allow(app, "*")
    for n in range(11):
        _as(app.test_client(), f"0000-0001-0000-{1000 + n:04d}", f"Person {n}")
    page = client.get(f"/admin/accounts/{_uid(app)}").data.decode()
    search = page.index('data-filter-select="supervisor"')
    form = page.index('<input type="hidden" name="action" value="supervisor">')
    assert search < form and page.rfind("<form", 0, search) < page.rfind("</form>", 0, search)
    assert 'id="supervisor" name="supervisor" data-submit-on-change size="6"' in page


def test_a_failing_notice_holds_up_neither_ends_nor_deletions(app, client, monkeypatch):
    """Gesamtaudit 2026-10-18, FUNC-02: run called notice, end and deletion one after the other; a notice that kept
    failing (the mail) kept the ended accounts from being blocked and deleted. Each now runs on its own, and one
    account that fails to go leaves the others."""
    from app import account_types

    allow(app, OTHER)
    _as(app.test_client(), OTHER)
    _ended(app)

    def broken(now=None):
        raise RuntimeError("the mail server is gone")

    monkeypatch.setattr(account_types, "notify_due", broken)
    with app.test_request_context():
        account_types.run()
        row = db.session.scalar(db.select(UserLogin).filter_by(user_id=_uid(app)))
        assert row.status == UserLogin.BLOCKED and row.block_reason == "expired"


def test_one_account_that_fails_to_go_leaves_the_others(app, client, monkeypatch):
    """FUNC-02: delete_due goes on after an unexpected error with one account."""
    from app import account_types, accounts

    third = "0000-0000-0000-0124"
    allow(app, OTHER, third)
    _as(app.test_client(), OTHER)
    _as(app.test_client(), third, "Dritte")
    _ended(app, days=400)
    _ended(app, orcid=third, days=400)
    first = min(_uid(app), _uid(app, third))
    real = accounts.delete

    def delete(user, *a, **k):
        if user.id == first:
            raise RuntimeError("a file of this account resists")
        return real(user, *a, **k)

    monkeypatch.setattr(accounts, "delete", delete)
    with app.test_request_context():
        account_types.expire_due()
        assert account_types.delete_due() == 1
        assert db.session.get(User, first) is not None
        assert db.session.scalar(db.select(db.func.count(User.id)).where(User.orcid.in_([OTHER, third]))) == 1


def test_a_change_of_type_keeps_a_create_given_on_the_account_page(app, client):
    """Ralf 2026-10-28: "Überall anlegen", then the type "Mitarbeitende" (all "edit") took "create" away without a
    word, and so did the unchanged field saved again. Now "create" stays where the type says "edit"; a type that
    says "view" lowers it; the same type again changes nothing."""
    from app import kind_access

    allow(app, OTHER)
    _as(app.test_client(), OTHER)
    uid, staff, master = _uid(app), _type(app, "staff"), _type(app, "master")
    client.post(f"/admin/accounts/{uid}", data={"action": "may_create", "value": "0"})
    client.post(f"/admin/accounts/{uid}", data={"action": "may_create", "value": "1"})
    assert _login(app).account_type_id is None
    for _ in range(2):  # a different type, then the same again (the autosave field sent once more)
        client.post(f"/admin/accounts/{uid}", data={"action": "account_type", "account_type": str(staff)})
        assert set(kind_access.full(_login(app).kinds).values()) == {"create"}
    assert "legt an" in client.get("/admin/people").data.decode()
    client.post(f"/admin/accounts/{uid}", data={"action": "account_type", "account_type": str(master)})
    assert kind_access.full(_login(app).kinds) == {"survey": "create", "live": "view", "interview": "view",
                                                   "diary": "view"}
