"""Accounts: the tests of the earlier audit and review files about this area (sorted by area,
architecture concept 2026-10-12, G). Each block names the file it came from; its helpers are in
tests/helpers/audit_<file>.py."""
from tests.accounts.password_setup import confirmed_password
from tests.helpers.audit_audit13_rest import Survey, db, timedelta, utcnow  # noqa: F401
from tests.helpers.audit_audit_2026_10_06_a import AccessLog, ConstructShare, Item, OTHER as OTHER__audit_2026_10_06_a, User, _insert, _linked_question, _member as _member__audit_2026_10_06_a, _own_survey, _person as _person__audit_2026_10_06_a, choice, linked  # noqa: F401
from tests.helpers.audit_audit_2026_10_06_d import _no_failures_from_other_tests, GOOD, MAIL, ME as ME__audit_2026_10_06_d, OTHER as OTHER__audit_2026_10_06_d, SurveyMember, THIRD as THIRD__audit_2026_10_06_d, UserLogin, WorkGroup, WorkGroupMember, _as as _as__audit_2026_10_06_d, _may_create, _user, allow, guest, notify, passwords, pytest  # noqa: F401
from tests.helpers.audit_audit_2026_10_06_e import OTHER as OTHER__audit_2026_10_06_e, _add, _group as _group__audit_2026_10_06_e, _log, _member_id, _sign_in, audit  # noqa: F401
from tests.helpers.audit_audit_2026_10_06_g import OnceValue, Response, once_survey, panel  # noqa: F401
from tests.helpers.audit_audit_2026_10_06_reste import ME as ME__audit_2026_10_06_reste, OTHER as OTHER__audit_2026_10_06_reste, THIRD as THIRD__audit_2026_10_06_reste, _as as _as__audit_2026_10_06_reste, _calc_in, _in_group, _may, _set, _stored, _uid, accounts, command, flow, migrations, rows, signin, text  # noqa: F401
from tests.helpers.audit_audit_2026_10_12_a import E as E__audit_2026_10_12_a, MultiDict, _answers, _scale, add, export, random, sem_model  # noqa: F401
from tests.helpers.audit_audit_2026_10_12_c import API, Enrolment, _activate, _bearer, _enrol, _new, datetime, diary, timezone  # noqa: F401
from tests.helpers.audit_audit_2026_10_12_d import Construct, ConstructCitation, ResearchProject, lifecycle  # noqa: F401
from tests.helpers.audit_audit_2026_10_12_h import _fit, _products, np  # noqa: F401
from tests.helpers.audit_audit_small3 import Feedback  # noqa: F401
from tests.helpers.audit_cr8_a import app  # noqa: F401
from tests.helpers.audit_cr8_b import _taken, needs_sqlite, retention, sqlite3  # noqa: F401
from tests.helpers.audit_cr8_c import ContactEntry, PG_URL, SplitSession, needs_pg  # noqa: F401
from tests.helpers.audit_cr8_g import NODE, OTHER as OTHER__cr8_g, STATIC, _js, _member as _member__cr8_g, _person as _person__cr8_g, json, live_add, live_make, subprocess  # noqa: F401
from tests.helpers.audit_cr8_i import ME as ME__cr8_i, OTHER as OTHER__cr8_i, os  # noqa: F401
from tests.helpers.audit_review2_reste import E as E__review2_reste, OTHER as OTHER__review2_reste, PreviewComment, _comment, colleague  # noqa: F401
from tests.helpers.audit_review_fixes import CONSENT_OK, _start, _state, runtime, test_flow  # noqa: F401
from tests.helpers.audit_review_medium import E as E__review_medium, OTHER as OTHER__review_medium, SurveyGroup, _answer, _as as _as__review_medium, _group as _group__review_medium, _member as _member__review_medium  # noqa: F401
from tests.helpers.audit_s9_one_source import CSS, PHYSICAL_OK, _ltr_classes, copy, re  # noqa: F401
from tests.conftest import CREATE_ALL


# --- from tests/audits/test_audit13_rest.py: Audit Daten und Berechnungen 2026-10-12, package I (exports, choice models, elements, rest): E-3, E-4, E-5, E-6, ---


def test_e5_snapshot_named_in_local_time(app, client, survey):
    from app import snapshots
    from app.timeutil import to_local

    with app.test_request_context():
        s = db.session.get(Survey, 1)
        name = snapshots.save(s)
        local = to_local(utcnow()).replace(tzinfo=None)
        [(found, _, when)] = [x for x in snapshots.listing(s) if x[0] == name]
        assert abs(when - local) < timedelta(minutes=2)


# --- from tests/audits/test_audit_2026_10_06_a.py: Security audit 2026-10-06, package A "Rollen und Objekte": S1, S2, S9, S10, S12, S22, H5 and the side finding on ---


def test_s1_viewer_of_a_using_survey_cannot_reach_it(app, client, survey):
    key = _linked_question(app, client)
    viewer = _person__audit_2026_10_06_a(app, OTHER__audit_2026_10_06_a, "Wee", may_create=True)
    _member__audit_2026_10_06_a(app, 1, OTHER__audit_2026_10_06_a, "viewer")
    sid, block = _own_survey(app, viewer, "Wees Befragung")
    mine = _insert(app, viewer, sid, block, key)
    choice(viewer, sid, mine, question="Von W geändert")
    with app.app_context():
        assert db.session.get(Item, 2).texts["de"]["question"] == "Frage"
        assert linked.adapted(db.session.get(Item, mine))


def test_s1_translation_page_follows_the_rule(app, client, survey):
    key = _linked_question(app, client)
    with app.app_context():
        db.session.add(ConstructShare(construct_id=linked.get(key).id, orcid=OTHER__audit_2026_10_06_a))
        db.session.commit()
    other = _person__audit_2026_10_06_a(app, OTHER__audit_2026_10_06_a, "Zed", may_create=True)
    sid, block = _own_survey(app, other, "Zeds Befragung")
    mine = _insert(app, other, sid, block, key)
    with app.app_context():
        item, survey_row = db.session.get(Item, mine), db.session.get(Survey, sid)
        item.texts = {**item.texts, "en": {**(item.texts.get("en") or {}), "question": "Changed by Z"}}
        with app.test_request_context():
            assert linked.save_survey(survey_row, db.session.scalar(db.select(User).filter_by(orcid=OTHER__audit_2026_10_06_a))) == 0
        db.session.commit()
        assert (db.session.get(Item, 2).texts.get("en") or {}).get("question") != "Changed by Z"
        assert linked.adapted(db.session.get(Item, mine))


def test_s2_vignette_long_format_needs_editor_and_is_logged(app, client, survey):
    from tests.analysis.test_vignettes import _with_answers

    node, _code = _with_answers(app, client, persons=3)
    _person__audit_2026_10_06_a(app, OTHER__audit_2026_10_06_a, "Viewer")
    _member__audit_2026_10_06_a(app, 1, OTHER__audit_2026_10_06_a, "viewer")
    viewer = _person__audit_2026_10_06_a(app, OTHER__audit_2026_10_06_a, "Viewer")
    assert viewer.get(f"/surveys/1/export/vignettes/{node['id']}.csv").status_code == 302
    page = viewer.get("/surveys/1/data").data.decode()
    assert "/export/vignettes/" not in page
    r = client.get(f"/surveys/1/export/vignettes/{node['id']}.csv")
    assert r.status_code == 200 and "resp_id" in r.data.decode()
    with app.app_context():
        assert db.session.scalar(db.select(AccessLog).filter_by(action="Datenexport Vignetten (Langformat)")) is not None


# --- from tests/audits/test_audit_2026_10_06_d.py: Security audit 2026-10-06, package D "Konten": the right to create surveys of invited people (S11), blocking ends ---


def test_an_invitation_no_longer_hands_out_the_right_to_create(app, client, survey):
    app.config["NEW_ACCOUNTS_MAY_CREATE"] = True
    with app.app_context():
        db.session.add(SurveyMember(survey_id=survey, orcid=OTHER__audit_2026_10_06_d, role="viewer", position=1))
        db.session.commit()
    invited = app.test_client()
    _as__audit_2026_10_06_d(invited, OTHER__audit_2026_10_06_d, "Eingeladene")
    with app.app_context():
        row = db.session.get(UserLogin, _user(app, OTHER__audit_2026_10_06_d).id)
        assert row is None or not row.kinds or "create" not in row.kinds.values()
    assert not _may_create(app, OTHER__audit_2026_10_06_d)
    assert "page-head-actions" not in invited.get("/dashboard/").data.decode()
    # whoever the configuration lets in gets it at the first sign-in, and keeps it when the list changes
    allow(app, THIRD__audit_2026_10_06_d)
    _as__audit_2026_10_06_d(app.test_client(), THIRD__audit_2026_10_06_d, "Freigeschaltete")
    allow(app)
    with app.app_context():
        assert db.session.get(UserLogin, _user(app, THIRD__audit_2026_10_06_d).id).kinds == CREATE_ALL
    # an account a site admin makes gets the tick of the form
    client.post("/admin/accounts/new", data={"name": "Gast", "email": MAIL, "may_create": "1"})
    with app.app_context():
        assert db.session.scalar(db.select(UserLogin).filter_by(email=MAIL)).kinds == CREATE_ALL


def test_the_slack_message_says_whether_the_new_account_may_create(app, client, monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "slack", lambda text, blocks=None: sent.append(text))
    _as__audit_2026_10_06_d(app.test_client(), OTHER__audit_2026_10_06_d, "Eingeladene")
    with app.test_request_context():
        notify.new_user(db.session.scalar(db.select(User).filter_by(orcid=OTHER__audit_2026_10_06_d)))
        notify.new_user(db.session.scalar(db.select(User).filter_by(orcid=ME__audit_2026_10_06_d)))
    assert sent[0].endswith("ohne Anlegerecht") and sent[1].endswith("mit Anlegerecht")


@pytest.mark.parametrize("path, field", [("/account/password", "current"), ("/account/delete", "password")])
def test_guessing_the_current_password_ends_the_session(app, client, guest, path, field):  # noqa: F811
    guest.environ_base["REMOTE_ADDR"] = "203.0.113.5"
    data = {field: "falsches Passwort", "password": "ein ganz neuer langer Satz", "repeat": "ein ganz neuer langer Satz",
            "name": "Gast"} if field == "current" else {field: "falsches Passwort"}
    for _ in range(passwords.MAX_FAILURES - 1):
        assert "stimmt nicht" in guest.post(path, data=data, follow_redirects=True).data.decode()
    assert guest.get("/account/").status_code == 200
    r = guest.post(path, data=data)
    assert r.status_code == 302 and "/auth/sign-in" in r.headers["Location"]
    assert guest.get("/account/").status_code == 302                          # signed out
    # the sign-in from this sender pauses too, also with the right password
    assert guest.post("/auth/sign-in", data={"email": MAIL, "password": GOOD}).status_code == 401


def test_the_right_current_password_still_works(app, client, guest):  # noqa: F811
    guest.environ_base["REMOTE_ADDR"] = "203.0.113.5"
    for _ in range(3):
        guest.post("/account/password", data={"current": "falsch falsch", "password": "x", "repeat": "x"})
    new = "ein ganz neuer langer Satz"
    page = guest.post("/account/password", data={"current": GOOD, "password": new, "repeat": new},
                      follow_redirects=True).data.decode()
    assert "Passwort gespeichert" in page


def test_site_admins_may_sign_in_with_a_password_too(app, client):
    """In place of H1 (Ralf 2026-10-09): site admins add a password like everyone, and sign in with it when ORCID is
    not at hand, together with a code from an authenticator app (Ralf 2026-10-09)."""
    data = {"email": "admin@example.org", "password": GOOD, "repeat": GOOD}
    page = confirmed_password(app, client, data).data.decode()
    assert "nur mit ORCID" not in page
    with app.app_context():
        admin = db.session.scalar(db.select(User).filter_by(orcid=ME__audit_2026_10_06_d))
        assert passwords.has_password(admin)
    # the password alone does not sign a site admin in: a code from the app comes with it (tests/accounts/test_admin_2fa.py)
    c = app.test_client()
    r = c.post("/auth/sign-in", data={"email": "admin@example.org", "password": GOOD})
    assert r.status_code == 401 and "Authenticator-App" in r.data.decode()


def test_the_one_time_link_page_lets_its_own_form_send_the_referer(app):
    """Over https the CSRF check wants the Referer of the form's POST: "no-referrer" broke setting the password on
    polltergeist.de (2026-10-09). "same-origin" still passes nothing on to other sites."""
    with app.app_context():
        admin = db.session.scalar(db.select(User).filter_by(orcid=ME__audit_2026_10_06_d))
        if admin is None:
            admin = User(orcid=ME__audit_2026_10_06_d, name="Admin")
            db.session.add(admin)
            db.session.flush()
        token = passwords.new_link_token(admin, None)
        db.session.commit()
    r = app.test_client().get(f"/auth/password/{token}")
    assert r.status_code == 200 and r.headers["Referrer-Policy"] == "same-origin"


# --- from tests/audits/test_audit_2026_10_06_e.py: Security audit 2026-10-06, package E: researcher texts of the delay discounting stay inside their data block (S3), ---


def test_every_logged_action_is_a_real_endpoint(app):
    endpoints = {rule.endpoint for rule in app.url_map.iter_rules()}
    assert set(audit.ACTIONS) <= endpoints and set(audit.ON_SUCCESS) <= set(audit.ACTIONS)
    for key in ("surveys.export_vignettes", "surveys.export_voice", "surveys.set_group_access", "groups.add_member",
                "groups.remove_member", "groups.set_role", "groups.share", "interview_results.project_file",
                "interview_transcribe.download", "interview_audio.listen", "accounts.delete_own"):
        assert key in audit.ACTIONS
    # only what data protection needs (Ralf, 2026-10-06)
    for key in ("auth.sign_in", "auth.orcid_callback", "auth.set_password", "accounts.password",
                "admin.experiments_change", "live.qa_csv"):
        assert key not in audit.ACTIONS
    # of these two only the sign-in address and deleting a group, written by the view (audit 2026-10-09, A3)
    assert {"admin.account", "admin.group_action"} <= set(audit.ON_SUCCESS)


def test_group_members_and_rights_are_logged(app, client, survey):
    gid = _group__audit_2026_10_06_e(client)
    _add(client, gid, OTHER__audit_2026_10_06_e, "Kollegin")
    mid = _member_id(client, gid, OTHER__audit_2026_10_06_e)
    client.post(f"/groups/{gid}/members/{mid}/role", data={"role": "lead"})
    client.post(f"/groups/{gid}/members/{mid}/remove")
    client.post(f"/admin/groups/{gid}", data={"action": "may_create", "value": "1"})
    rows = _log(app)
    assert [a for a, _ in rows] == ["Gruppe: Person aufgenommen", "Gruppe: Rolle geändert", "Gruppe: Person entfernt"]
    assert f"group_id={gid}" in rows[0][1] and f"orcid={OTHER__audit_2026_10_06_e}" in rows[0][1] and "role=lead" in rows[1][1]
    page = client.get("/admin/log").data.decode()
    assert "Gruppe: Person aufgenommen" in page and "Gruppe geändert (Administration)" not in page


def test_deleted_accounts_are_logged_sign_ins_and_passwords_not(app, client, guest):  # noqa: F811
    assert [a for a, _ in _log(app)] == ["Konto angelegt"]                    # the redeemed one-time link: no entry
    c = app.test_client()
    assert _sign_in(c, password="falsch").status_code == 401                  # a wrong password: no entry
    assert _sign_in(c).status_code == 302
    data = {"current": GOOD, "password": "noch ein langer neuer Satz", "repeat": "noch ein langer neuer Satz"}
    guest.post("/account/password", data={**data, "current": "falsch"})          # refused: no entry
    guest.post("/account/password", data=data)
    rows = _log(app)
    assert [a for a, _ in rows] == ["Konto angelegt"]                         # sign-in and new password: no entry
    assert all("passw" not in d.lower() and GOOD not in d for _, d in rows)    # never a password in the detail
    guest.post("/account/delete", data={"password": "falsch"})                 # refused: no entry
    assert guest.post("/account/delete", data={"password": "noch ein langer neuer Satz"}).status_code == 302
    with app.app_context():
        last = db.session.scalar(db.select(AccessLog).order_by(AccessLog.id.desc()))
        assert last.action == "Eigenes Konto gelöscht" and "Gast" in last.who and last.user_id is not None
    assert len(_log(app)) == 2
    page = client.get("/admin/log").data.decode()
    assert "Eigenes Konto gelöscht" in page and "Einmal-Link eingelöst" not in page and "Anmeldung (Passwort)" not in page


# --- from tests/audits/test_audit_2026_10_06_g.py: Audit 2026-10-06, package G: performance of the database and of taking part. P3 (index for the counts of ---


def test_the_database_keeps_a_claimed_id_unique(app, client, survey):
    """T-11 without the advisory lock: of two participations inserted with the same ID, the second claim fails."""
    once_survey(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        first, second = (Response(survey_id=s.id, params={"PID": "p7"}, answers={}, source="real", is_preview=False)
                         for _ in range(2))
        db.session.add(first)
        db.session.flush()
        assert panel.claim(s, first) is None
        db.session.add(second)  # sent at the same moment: inserted before the first was committed
        db.session.flush()
        assert panel.claim(s, second) == "PID"
        assert [r.once for r in db.session.scalars(db.select(OnceValue).order_by(OnceValue.response_id))] == [True, False]
        db.session.rollback()


# --- from tests/audits/test_audit_2026_10_06_reste.py: Security audit 2026-10-06, the rests S17 and H5: the setting "eigene Befragungen anlegen" on an account has three ---


def test_s17_the_admins_no_outweighs_a_group(app, client):
    """Since 2026-10-18 a group gives "Anlegen" once on joining (konzept-stufe-anlegen-2026-10-18.md, E3); an admin's
    "Nirgends anlegen" takes it back and holds against the sign-in list."""
    other = app.test_client()
    _as__audit_2026_10_06_reste(other, OTHER__audit_2026_10_06_reste, "Kollegin")
    uid = _uid(app, OTHER__audit_2026_10_06_reste)
    _in_group(app, OTHER__audit_2026_10_06_reste)
    assert _may(app, OTHER__audit_2026_10_06_reste)                          # the group gave it on joining
    page = client.get(f"/admin/accounts/{uid}").data.decode()
    assert 'value="1">Überall anlegen</button>' in page and 'value="0">Nirgends anlegen</button>' in page

    page = _set(client, uid, "0")
    assert "legt keine eigenen Befragungen mehr an" in page
    assert _stored(app, OTHER__audit_2026_10_06_reste) is False and not _may(app, OTHER__audit_2026_10_06_reste)
    assert "page-head-actions" not in other.get("/dashboard/").data.decode()
    assert other.post("/groups/new", data={"name": "Eigene"}).status_code == 403
    assert "legt an" not in client.get("/admin/people").data.decode().split("Kollegin", 1)[1].split("</td>", 1)[0]

    assert "legt jetzt Befragungen jeder Art an" in _set(client, uid, "1")
    assert _stored(app, OTHER__audit_2026_10_06_reste) is None and _may(app, OTHER__audit_2026_10_06_reste)
    assert client.post(f"/admin/accounts/{uid}", data={"action": "may_create", "value": "ja"}).status_code == 400
    assert client.post(f"/admin/accounts/{uid}", data={"action": "may_create", "value": "groups"}).status_code == 400


def test_s17_new_accounts_and_the_sign_in_list_leave_an_explicit_no(app, client):
    app.config["NEW_ACCOUNTS_MAY_CREATE"] = True
    with app.test_request_context():
        me = db.session.scalar(db.select(User).filter_by(orcid=ME__audit_2026_10_06_reste))
        ticked = accounts.create_local("Mit", "mit@example.org", "", True, me)
        unticked = accounts.create_local("Ohne", "ohne@example.org", "", False, me)
        db.session.commit()
        assert db.session.get(UserLogin, ticked.id).kinds == CREATE_ALL
        assert db.session.get(UserLogin, unticked.id).kinds is None            # all four at "edit"
    allow(app, THIRD__audit_2026_10_06_reste)
    _as__audit_2026_10_06_reste(app.test_client(), THIRD__audit_2026_10_06_reste, "Dritte")
    assert _may(app, THIRD__audit_2026_10_06_reste)                          # the first sign-in, as before
    allow(app)
    _as__audit_2026_10_06_reste(app.test_client(), OTHER__audit_2026_10_06_reste, "Kollegin")
    _set(client, _uid(app, OTHER__audit_2026_10_06_reste), "0")
    client.post("/admin/admit", data={"action": "add", "orcid": OTHER__audit_2026_10_06_reste})
    assert _stored(app, OTHER__audit_2026_10_06_reste) is False and not _may(app, OTHER__audit_2026_10_06_reste)
    with app.app_context():
        user = db.session.get(User, _uid(app, OTHER__audit_2026_10_06_reste))
        signin._may_create([user])                                            # listing the iD keeps the admin's "no"
        db.session.commit()
    assert not _may(app, OTHER__audit_2026_10_06_reste)


@pytest.mark.sqlite_foreign_keys_off  # goes down and up again: SQLite's batch mode (tests/conftest.py, B8)
def test_s17_migration_0013_changes_nothing_for_anybody(app):
    """Going down to 0012 and up again: "a group may give it" was false and is NULL, true stays true."""
    _as__audit_2026_10_06_reste(app.test_client(), OTHER__audit_2026_10_06_reste, "Kollegin")
    _as__audit_2026_10_06_reste(app.test_client(), THIRD__audit_2026_10_06_reste, "Dritte")
    with app.app_context():
        db.session.add(UserLogin(user_id=_uid(app, OTHER__audit_2026_10_06_reste), may_create=None))
        db.session.add(UserLogin(user_id=_uid(app, THIRD__audit_2026_10_06_reste), may_create=True))  # the column the migrations read
        db.session.commit()
        db.session.remove()
        migrations._run(command.downgrade, "0012")
        with db.engine.connect() as connection:
            found = dict(connection.execute(text("SELECT user_id, may_create FROM user_login")).fetchall())
        assert not found[_uid(app, OTHER__audit_2026_10_06_reste)] and found[_uid(app, OTHER__audit_2026_10_06_reste)] is not None and found[_uid(app, THIRD__audit_2026_10_06_reste)]
        migrations._run(command.upgrade)
        assert migrations.current() == migrations.head()
        with db.engine.connect() as connection:
            assert migrations.differences(connection) == []
            found = dict(connection.execute(text("SELECT user_id, may_create FROM user_login")).fetchall())
        assert found[_uid(app, OTHER__audit_2026_10_06_reste)] is None and found[_uid(app, THIRD__audit_2026_10_06_reste)]
    assert not _may(app, OTHER__audit_2026_10_06_reste)
    _in_group(app, OTHER__audit_2026_10_06_reste)
    assert _may(app, OTHER__audit_2026_10_06_reste)                          # joining the group gives "create" (0080, E3)


def test_h5_rows_read_a_rechenwert_of_their_own_survey_only(app, client, survey):
    sid, source, calc = _calc_in(app, client, "Andere")
    shown_to_some = [{"id": "s1", "when": {"item": calc, "op": "gt", "value": "10"}}, {"id": "s2"}]
    with app.app_context():
        own, other = db.session.get(Survey, sid), db.session.get(Survey, 1)
        assert rows.hidden(shown_to_some, {source: 5}, own) == set()          # 60 > 10: shown
        assert rows.hidden(shown_to_some, {source: 5}, other) == {"s1"}       # another survey's Rechenwert: unread
        assert rows.hidden(shown_to_some, {source: 5}, None) == {"s1"}
        assert flow.computed_value(calc, {source: 5}, None) is None
        assert flow.computed_value(calc, {source: 5}, own) == 60


# --- from tests/audits/test_audit_2026_10_12_a.py: Audit Daten und Berechnungen 2026-10-12, package A (crashes and wrong figures): S-1, S-3, W-1, M-1, SEM-1, SEM-2, ---


def test_group_moderator_also_in_a_product(app, client, survey):
    for number, code, k in ((2, "tb", 4), (3, "einst", 3), (4, "absicht", 3)):
        _scale(client, number, code, k)
    add(client, "single_choice")
    client.post(f"{E__audit_2026_10_12_a}/items/5", data=MultiDict({"code": "grp", "question": "grp?", "option_id": ["a", "b"],
                                                "option_code": ["1", "2"], "option_label": ["G1", "G2"]}))
    with app.app_context():
        s = db.session.get(Survey, 1)
        rng = random.Random(4)
        for answers in _answers(rng, 400):
            answers["5"] = rng.choice(["1", "2"])
            s.responses.append(Response(source="real", status=Response.COMPLETED, answers=answers))
        db.session.commit()
    client.post("/surveys/1/researchdesign", data=MultiDict({
        "research_question": "x", "goal": "correlation", "design": "cross",
        "h_id": ["", "", ""], "h_text": ["H1", "H2", "H3"],
        "h_kind": ["mediation", "moderation", "moderation"], "h_dir": ["", "", ""],
        "h_iv": ["tb", "tb", "tb"], "h_dv": ["absicht", "absicht", "einst"],
        "h_mv": ["einst", "", ""], "h_wv": ["", "grp", "grp"],
        "h_analysis": ["", "sem", "sem"], "sem_method": "cb"}))
    with app.app_context():
        s = db.session.get(Survey, 1)
        model = sem_model.build(s)
        g = next(iter(model["groups"].values()))
        assert not any("×" in a or "×" in b for a, b in g["spec"]["paths"])
        table = export.rows(s, False, ("real",), None, True, with_hb=False)
        result = sem_model.estimate(s, table)
        fit = next(iter(result["group_fits"].values()))
        assert "error" not in fit and fit["n"][0] + fit["n"][1] == 400
    assert client.get("/surveys/1/results?go=1").status_code == 200


# --- from tests/audits/test_audit_2026_10_12_c.py: Audit data and calculations 2026-10-12, package C (diary study): E-1, E-2, D-1 to D-7, D-10, D-12 to D-15 and ---


def test_offline_self_report_without_uuid_is_stored_once(app, client):
    code = _new(app, client)
    phone, secret = _enrol(app, code)
    _activate(app)
    with app.app_context():
        survey = db.session.get(Survey, 1)
        m1 = {**diary.module(survey, "m1"), "trigger": {"t": "self", "label": {"de": "Jetzt"}, "max_per_day": 0}}
        survey.sampling = {**survey.sampling, "modules": [m1]}
        db.session.commit()
    kept = phone.post(f"{API}/offline/m1", json={}, headers=_bearer(secret)).json
    with app.app_context():
        person = db.session.scalar(db.select(Enrolment))
        person.settings = {**(person.settings or {}), "offline_at": (utcnow() - timedelta(hours=1)).isoformat()}
        from app.models import BrowserReceipt
        for receipt in db.session.scalars(db.select(BrowserReceipt).filter_by(survey_id=person.survey_id,
                                                                              scope=f"offline:{person.id}")):
            receipt.data = {**receipt.data, "at": person.settings["offline_at"]}
        db.session.commit()
        item = next(i for b in db.session.get(Survey, 1).blocks if diary.part_of(b) == "m1" for i in b.items)
    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    sent = {"module": "m1", "started": now - 120_000, "completed": now - 60_000, "version": kept["version"],
            "form": {f"i{item.id}": ["4"], "nav": ["next"]}}
    assert phone.post(f"{API}/offline", json=sent, headers=_bearer(secret)).json["status"] == "stored"
    assert phone.post(f"{API}/offline", json=sent, headers=_bearer(secret)).json["status"] == "stored"
    later = {**sent, "started": now - 50_000, "completed": now - 40_000}  # another report: stored
    assert phone.post(f"{API}/offline", json=later, headers=_bearer(secret)).json["status"] == "stored"
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id)).where(Response.part == "m1")) == 2


# --- from tests/audits/test_audit_2026_10_12_d.py: Audit data and calculations 2026-10-12, package D (deletion, quotas, kept results): E-7 to E-11. ---


def test_delete_clears_citation_and_project_references(app, client, survey):
    with app.app_context():
        user = db.session.scalar(db.select(User).limit(1))
        construct = db.session.scalar(db.select(Construct).limit(1))
        cit = ConstructCitation(construct_id=construct.id, doi="10.1000/x", meta={}, survey_id=1)
        proj = ResearchProject(user_id=user.id, name="P", survey_id=1)
        db.session.add_all([cit, proj])
        db.session.commit()
        cid, pid = cit.id, proj.id
        lifecycle.delete(db.session.get(Survey, 1))
        db.session.commit()
        assert db.session.get(ConstructCitation, cid).survey_id is None
        assert db.session.get(ResearchProject, pid).survey_id is None


def test_live_session_checks_the_pid(app, client, survey):
    from flask import session

    from app import live

    with app.app_context():
        other = Response(survey_id=1, source="real", status=Response.COMPLETED, pid="1-fremd", answers={})
        db.session.add(other)
        db.session.commit()
        s = db.session.get(Survey, 1)
        st = {"run": {"id": "r1", "name": "Lauf"}}
        key = f"live:{s.id}:r1"
        with app.test_request_context("/"):
            session[key], session[key + ":pid"] = other.id, "1-meins"  # the old device's own one was deleted
            assert live._response(s, st) is None
            session[key + ":pid"] = "1-fremd"
            assert live._response(s, st).id == other.id
            del session[key + ":pid"]  # sessions from before the pid was noted
            assert live._response(s, st).id == other.id


# --- from tests/audits/test_audit_2026_10_12_h.py: Audit Daten und Berechnungen 2026-10-12, package H (SEM rest and R script): SEM-3 to SEM-7, SEM-9 to SEM-11, ---


def test_a_moderator_with_two_groups_gives_the_effects_per_group():
    """Decision 8: a 0/1 moderator in an interaction term: slopes and conditional indirect effects at w = 0 and w = 1
    (Hayes, 2015). Reference values: (a1 + a3·(g − mean w))·sd_x/sd_m = 0.369343 / 0.670717, times b·sd_m/sd_y 0.183293 /
    0.332855."""
    rng = np.random.default_rng(7)
    n = 600
    x = rng.normal(0, 1, n)
    w = (rng.random(n) < 0.35).astype(float)
    m = 0.4 * x + 0.3 * w + 0.35 * x * w + rng.normal(0, 0.8, n)
    y = 0.5 * m + 0.1 * x + rng.normal(0, 0.8, n)
    cols, data = [], []
    for name, f, loads in (("x", x, (1, .8, .9)), ("m", m, (1, .8, .9)), ("y", y, (1, .7, .8))):
        for k, load in enumerate(loads, 1):
            cols.append(f"{name}{k}")
            data.append(3 + load * f + rng.normal(0, 0.55, n))
    cols.append("w")
    data.append(w)
    X = np.column_stack(data)
    XP = _products(X, {c: k for k, c in enumerate(cols)}, [(f"x{k}", "w") for k in (1, 2, 3)])
    spec = {"latent": {"X": ["x1", "x2", "x3"], "M": ["m1", "m2", "m3"], "Y": ["y1", "y2", "y3"],
                       "X×W": ["x1×w", "x2×w", "x3×w"]},
            "observed": ["w"], "paths": [("X", "M"), ("w", "M"), ("X×W", "M"), ("M", "Y"), ("X", "Y")],
            "indirect": [("X", "M", "Y")],
            "products": {"X×W": {"x": "X", "w": "w", "kind": "lo", "pairs": [["x1", "w"], ["x2", "w"], ["x3", "w"]]}},
            "modmed": [("a", "X", "M", "Y", "X×W")]}
    result = _fit(XP, cols + ["x1×w", "x2×w", "x3×w"], spec)
    result["binary"] = {"w": {"mean": float(w.mean()), "labels": ["nein", "ja"]}}
    mod = {"x": "X", "w": "w", "y": "M", "xw": "X×W"}
    slopes = sem_model.simple_slopes(result, mod)
    assert [(k, label) for k, label, _ in slopes] == [("g0", "nein"), ("g1", "ja")]
    assert abs(slopes[0][2] - 0.369343) < 2e-5 and abs(slopes[1][2] - 0.670717) < 2e-5
    idx = sem_model.modmed_index(result, {"modmed": ("a", "X", "M", "Y", "X×W"), "moderation": mod})
    assert [round(v, 5) for _, _, v in idx["conditional"]] == [0.18329, 0.33286]
    assert sem_model._slope_label("g1", "ja", "w") == "w = „ja“"


# --- from tests/audits/test_audit_rest.py: Remaining fixes from the audit of 2026-10-01 (S5, S9, S12 to S15, S17, S18, D-2, D-6, D-7, D-9, D-12 and small ones). ---


def test_a_running_survey_is_never_deleted_with_the_account(app, client, survey):
    with app.app_context():
        from app.models import User

        s = db.session.get(Survey, 1)
        me = db.session.scalar(db.select(User))
        assert [e["survey"].id for e in accounts.plan(me)["drafts"]] == [1]
        s.status = Survey.RUNNING                                # in the field, nothing stored yet
        db.session.commit()
        plan = accounts.plan(me)
        assert not plan["drafts"] and [e["survey"].id for e in plan["running"]] == [1]


# --- from tests/audits/test_audit_small.py: Smaller points of the data audit: codebook values, orders in loops, screen-out times, long JSON in .sav. ---


def test_sample_compare_keeps_the_largest_group_shown(app, client, survey):
    from tests.analysis.test_sample_compare import test_sample_compare_threshold_own_answer_and_export as build

    build(app, client, survey)                                      # items: 2 matrix, 3 choice, 5 comparison
    with app.app_context():
        item = db.session.get(Item, 5)
        form = MultiDict([("i5_n_2", "0"), ("i5_n_2", "7"), ("i5_n_2", "6"), ("i5_n_3", "0")])
        assert item.kind.parse_answer(item, form) == {"2": 7, "3": 0}
        assert item.kind.parse_answer(item, {"i5_n_2": "4"}) == {"2": 4}


# --- from tests/audits/test_audit_small3.py: Small things from the audit of 2026-10-01 (package 7): patterns that keep a server busy, a content policy, ---


@pytest.mark.parametrize("pattern", [r"[A-Z]{2}\d{4}", r"\d{5}", r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
                                     "(ja|nein|vielleicht)", r"\d{1,3}(\.\d{3})*(,\d+)?", r".{3,50}", r"\w+", "[a-zäöüß ]+",
                                     "(DE)?[0-9]{5}", "[", ""])
def test_ordinary_patterns_are_left_alone(pattern):
    from app.items.text import risky_pattern

    risky_pattern.cache_clear()
    assert not risky_pattern(pattern)


def test_person_search_in_the_wizard_is_for_admins_of_the_survey(app, client, survey, monkeypatch):
    from app import orcid_api

    calls = []
    monkeypatch.setattr(orcid_api, "search", lambda q, rows=10: calls.append(q) or [])
    client.post("/surveys/1/members", data={"orcid": "0000-0002-1825-0097", "name": "Kollegin", "role": "viewer"})
    viewer = app.test_client()
    viewer.get("/auth/dev-login?orcid=0000-0002-1825-0097&name=Kollegin")
    viewer.post("/terms/", data={"accept": "1"})
    assert viewer.get("/surveys/1/setup/team?q=Muster").status_code == 200 and calls == []
    assert client.get("/surveys/1/setup/team?q=Muster").status_code == 200 and calls == ["Muster"]


def test_feedback_per_day_is_limited(app, client):
    from app import feedback

    with app.app_context():
        uid = db.session.scalar(db.select(User.id))
        db.session.add_all([Feedback(user_id=uid, message=f"m{n}") for n in range(feedback.PER_DAY)])
        db.session.commit()
    r = client.post("/feedback/new", data={"message": "noch eine", "severity": "1"})
    assert "schon 50 Rückmeldungen" in r.data.decode()
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Feedback.id))) == feedback.PER_DAY


# --- from tests/audits/test_cr8_a.py: Code review 2026-10-07, package A "Falsche oder verlorene Daten": regression tests for T1, E1, E2, E8, E9, E10, ---


def test_k1_deleted_group_leaves_no_share_and_its_id_is_not_given_again(app):
    from app import ainstein, groups
    from app.models import ResearchProject, User, WorkGroup, WorkGroupMember

    with app.app_context():
        owner = User(orcid="0000-0000-0000-0001", name="Owner", id=101, number=101)
        member = User(orcid="0000-0000-0000-0002", name="Old member", id=102, number=102)
        stranger = User(orcid="0000-0000-0000-0003", name="Stranger", id=103, number=103)
        db.session.add_all([owner, member, stranger])
        old = WorkGroup(name="Projektgruppe", created_by_id=owner.id)
        old.members.append(WorkGroupMember(orcid=owner.orcid, user_id=owner.id, role="lead"))
        old.members.append(WorkGroupMember(orcid=member.orcid, user_id=member.id, role="member"))
        db.session.add(old)
        db.session.flush()
        project = ResearchProject(user_id=owner.id, name="Geheim", shares=[
            {"kind": "person", "orcid": member.orcid, "name": "Old member", "role": "view"},
            {"kind": "group", "group_id": old.id, "name": old.name, "role": "edit"}])
        db.session.add(project)
        db.session.commit()
        old_id, pid = old.id, project.id
        assert ainstein.role_of(project, member) == "edit"
        groups.remove(old)
        db.session.commit()
        assert [s["kind"] for s in db.session.get(ResearchProject, pid).shares] == ["person"]
        new = WorkGroup(name="Ganz andere Gruppe", created_by_id=stranger.id)
        new.members.append(WorkGroupMember(orcid=stranger.orcid, user_id=stranger.id, role="lead"))
        db.session.add(new)
        db.session.commit()
        assert new.id != old_id
        assert ainstein.role_of(db.session.get(ResearchProject, pid), stranger) is None


@pytest.mark.sqlite_foreign_keys_off  # goes down and up again: SQLite's batch mode (tests/conftest.py, B8)
def test_k1_migration_drops_shares_of_groups_that_are_gone(app):
    from alembic import command

    from app import migrations
    from app.models import ResearchProject, User, WorkGroup

    with app.app_context():
        owner = User(orcid="0000-0000-0000-0001", name="Owner", id=101, number=101)
        db.session.add(owner)
        group = WorkGroup(name="Bleibt", created_by_id=owner.id)
        db.session.add(group)
        db.session.flush()
        shares = [{"kind": "group", "group_id": group.id, "name": "Bleibt", "role": "view"},
                  {"kind": "group", "group_id": group.id + 50, "name": "Weg", "role": "edit"},
                  {"kind": "person", "orcid": "0000-0000-0000-0002", "name": "P", "role": "view"}]
        project = ResearchProject(user_id=owner.id, name="P", shares=shares)
        db.session.add(project)
        db.session.commit()
        pid = project.id
        migrations._run(command.downgrade, "0018")
        migrations._run(command.upgrade)
        db.session.expire_all()
        assert [s["name"] for s in db.session.get(ResearchProject, pid).shares] == ["Bleibt", "P"]


# --- from tests/audits/test_cr8_b.py: Code review 2026-10-07, package B: SQLite write locks and housekeeping (K2/D1, K3/D2, K4, E4, K8, T8/D6, K9). ---


def test_housekeeping_leaves_no_open_transaction(app):
    with app.app_context():
        lifecycle.close_due_surveys()  # nothing to do in a fresh database
        assert not db.session().in_transaction()


@needs_sqlite
def test_housekeeping_leaves_no_write_lock(app, tmp_path, monkeypatch):
    monkeypatch.setattr(lifecycle, "_last_check", 0.0)
    monkeypatch.setattr(lifecycle, "_last_rare", None)
    with app.app_context():
        lifecycle.maybe_close_due_surveys()
        other = sqlite3.connect(tmp_path / "test.db", timeout=0)
        try:
            other.execute("BEGIN IMMEDIATE")  # raised "database is locked" before
            other.rollback()
        finally:
            other.close()
        db.session.rollback()


def test_rare_sweeps_run_once_an_hour(app, monkeypatch):
    from app import audit, sessions

    calls = []
    monkeypatch.setattr(sessions, "sweep", lambda now: calls.append("sessions") or 0)
    monkeypatch.setattr(audit, "sweep", lambda now: calls.append("audit") or 0)
    monkeypatch.setattr(lifecycle, "_last_rare", None)
    with app.app_context():
        lifecycle.close_due_surveys()
        lifecycle.close_due_surveys()
    assert calls == ["sessions", "audit"]


def test_identifiers_leave_out_empty_values(app, client, survey):
    _taken(app, client)
    with app.app_context():
        rows = db.session.scalars(db.select(Response).filter_by(survey_id=1).order_by(Response.id)).all()
        rows[0].params = None  # stored as JSON null, which IS NOT NULL does not leave out
        rows[1].params = {}
        db.session.commit()
        assert retention._with_identifiers(db.session.get(Survey, 1)) == [rows[2].id]


# --- from tests/audits/test_cr8_c.py: Code review 2026-10-07, package C: counters and changes at the same moment (D3, D4, T2, K11, T7, R7, F8, F13, G7, ---


def test_sqlite_stores_an_entry_at_once_and_has_nothing_to_bundle(app, client, survey):
    from app import contact
    from tests.quotas.test_split import build, participate

    if PG_URL:
        pytest.skip("SQLite")
    uid = build(app, client)
    participate(app, uid, "1", "zora@x.de")
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(ContactEntry)) == 1
        assert db.session.scalar(db.select(db.func.count()).select_from(SplitSession)) == 0
        assert contact.bundle() == 0


@needs_pg
def test_postgres_bundles_the_entries_about_once_a_minute(app, client, survey):
    from app import contact, erasure, lifecycle
    from tests.quotas.test_split import build, participate

    uid = build(app, client)
    for mail in ("zora@x.de", "anna@x.de", "mia@x.de"):
        participate(app, uid, "1", mail)
    with app.app_context():
        s = db.session.get(Survey, 1)
        # nothing rewritten at a completion: the finished sections wait, and count as entries meanwhile
        assert db.session.scalar(db.select(db.func.count()).select_from(ContactEntry)) == 0
        assert contact.count(s) == 3 and sorted(e.answers["3"] for e in contact.entries(s)) == ["anna@x.de", "mia@x.de", "zora@x.de"]
        assert [e.answers["3"] for e in erasure.find(s, "mia@x.de")[1]] == ["mia@x.de"]     # found for a deletion
        db.session.rollback()
        lifecycle.close_due_surveys()                                                      # the housekeeping bundles
        rows = db.session.execute(text("SELECT id, xmin::text FROM contact_entry")).all()
        assert len(rows) == 3 and len({x for _, x in rows}) == 1
        assert db.session.scalar(db.select(db.func.count()).select_from(SplitSession)) == 0
    participate(app, uid, "2", "ben@x.de")
    with app.app_context():
        assert contact.bundle() == 1
        rows = db.session.execute(text("SELECT id, xmin::text FROM contact_entry")).all()
        assert len(rows) == 4 and len({x for _, x in rows}) == 1                           # all written anew at once
        assert sorted(e.answers["3"] for e in contact.entries(db.session.get(Survey, 1)))[0] == "anna@x.de"


# --- from tests/audits/test_cr8_e.py: Code review 2026-10-07, package E: computing time of results and exports. ---


def test_r5_group_comparison_is_kept_with_its_pool(app, client, survey, monkeypatch):
    from app import compare
    from tests.accounts.test_groupvars import randomized

    randomized(app, client, [((0,), "1", 1, 2)] * 6 + [((1,), "2", 4, 5)] * 6)
    calls = []
    real = compare.compute
    monkeypatch.setattr(compare, "compute", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    for url in ("/surveys/1/results?gruppen=rand:r1", "/surveys/1/results?gruppen=rand:r1&markierte=1",
                "/surveys/1/results?gruppen=rand:r1", "/surveys/1/results?gruppen=rand:r1&markierte=1"):
        assert client.get(url).status_code == 200
    assert len(calls) <= 2  # each pool computes its comparison once


# --- from tests/audits/test_cr8_g.py: Code review 2026-10-07, package G "Live, Editor-Skripte, Rechner": F1, F2, F7, F12, F16, F17, F20, F22, F23, F24, ---


def test_f16_viewer_sees_no_steering_and_steps_report_refusals(app, client):
    code, sid, block = live_make(app, client, "Nur ansehen")
    live_add(app, client, sid, block, "rating", question="Wie war es?")
    editor = client.get(f"/presenter/{code}/speaker").get_data(as_text=True)
    assert 'data-step="next"' in editor and "data-readonly" not in editor.split("<main", 1)[1].split(">", 1)[0]
    viewer = _person__cr8_g(app, OTHER__cr8_g, "Vera")
    _member__cr8_g(app, sid, OTHER__cr8_g, "viewer")
    for path in ("speaker", "screen"):
        page = viewer.get(f"/presenter/{code}/{path}").get_data(as_text=True)
        assert "data-readonly" in page.split("<main", 1)[1].split(">", 1)[0]
        assert 'data-step="next"' not in page and 'data-toggle="voting"' not in page and "data-step-error" in page
    refused = viewer.post(f"/presenter/{code}/control", data={"action": "next"}, headers={"X-Requested-With": "fetch"})
    assert refused.status_code != 200 or not refused.is_json               # a refusal is never the JSON of a step taken
    assert client.post(f"/presenter/{code}/control", data={"action": "next"}, headers={"X-Requested-With": "fetch"}).is_json
    src = _js("live.js")
    assert "if (stepping || readonly) return;" in src and "if (!r.ok || r.redirected) stepFailed(" in src


@pytest.mark.skipif(NODE is None, reason="needs Node")
def test_g4_curve_without_mark_when_unreachable():
    script = """
const S = require(process.argv[1]);
function base(extra) { return Object.assign({effect: 0.5, f: 0.25, w: 0.3, rho: 0.5, alpha: 0.05, power: 0.8, onesided: false, groups: 3,
  ratio: 1, p1: 50, p2: 60, mode: "effect", cluster: NaN, icc: NaN, cv: NaN, share: NaN, dropout: 10, pop: NaN}, extra); }
const out = [["groups", 3], ["corr", 3], ["props", 4], ["groups", 6]].map(([d, n]) => { const c = S.curve(d, base({avail: n})); return c ? c.marks.length : null; });
console.log(JSON.stringify(out));
"""
    done = subprocess.run([NODE, "-e", script, str(STATIC / "samplesize.js")], capture_output=True, timeout=60)
    assert done.returncode == 0, done.stderr.decode()
    assert json.loads(done.stdout.decode()) == [0, 0, 0, 1]                # reachable with 6: the mark stays


# --- from tests/audits/test_cr8_i.py: Code review 2026-10-07, package I "Kleinere Fehler": R6, E3, E5, E6, E12, K5, K6, K7, T6, G11. ---


def test_e6_files_stay_when_the_change_is_rolled_back(app, client, survey):
    from app import designs, theme
    from app.models import Design
    from tests.ui.test_theme_logo import png, upload

    upload(client, png())
    with app.app_context():
        s = db.session.get(Survey, 1)
        logo = theme.clean(s.theme)["logo"]["file"]
        path = os.path.join(app.config["UPLOAD_FOLDER"], s.uid, logo)
        design = Design(owner_id=db.session.scalar(db.select(User.id).filter_by(orcid=ME__cr8_i)), name="D", data=theme.standard())
        db.session.add(design)
        db.session.commit()
        designs.apply_to(s, design)
        assert os.path.isfile(path)
        db.session.rollback()
        assert os.path.isfile(path)
        db.session.commit()
        assert os.path.isfile(path)


def test_k5_projects_follow_delete_and_link(app, client):
    from app import accounts

    client.get(f"/auth/dev-login?orcid={ME__cr8_i}&name=Philipp")
    with app.app_context():
        me = db.session.scalar(db.select(User).filter_by(orcid=ME__cr8_i))
        guest = User(name="Gast", pending=True)
        other = User(orcid=OTHER__cr8_i, name="Andere")
        db.session.add_all([guest, other])
        db.session.flush()
        gid, oid = guest.id, other.id
        p = ResearchProject(user_id=me.id, name="P",
                            shares=[{"kind": "person", "user_id": gid, "name": "Gast", "role": "edit"},
                                    {"kind": "person", "user_id": oid, "name": "Andere", "role": "view"},
                                    {"kind": "group", "group_id": 7, "name": "G", "role": "view"}],
                            comments=[{"id": "1", "user_id": gid, "name": "Gast", "text": "a"},
                                      {"id": "2", "user_id": oid, "name": "Andere", "text": "b"}])
        db.session.add(p)
        db.session.commit()
        # taking a pending account over: its entries move; an existing share of the account keeps the higher role
        accounts._rekey_projects(gid, oid, owner_id=-1)
        db.session.commit()
        p = db.session.get(ResearchProject, p.id)
        assert [(x.get("user_id"), x["role"]) for x in p.shares] == [(oid, "edit"), (None, "view")]
        assert [c["user_id"] for c in p.comments] == [oid, oid]
        # deleting: the shares end, the comments lose the name
        accounts._rekey_projects(oid)
        db.session.commit()
        p = db.session.get(ResearchProject, p.id)
        assert [x["kind"] for x in p.shares] == ["group"]
        assert [(c["user_id"], c["name"], c["text"]) for c in p.comments] == [(None, "", "a"), (None, "", "b")]


def test_k5_delete_account_ends_its_project_shares(app, client):
    from app import accounts

    other = app.test_client()
    other.get(f"/auth/dev-login?orcid={OTHER__cr8_i}&name=Andere")
    with app.app_context():
        me = db.session.scalar(db.select(User).filter_by(orcid=ME__cr8_i))
        user = db.session.scalar(db.select(User).filter_by(orcid=OTHER__cr8_i))
        p = ResearchProject(user_id=me.id, name="P", shares=[{"kind": "person", "user_id": user.id, "name": "A", "role": "view"}],
                            comments=[{"id": "1", "user_id": user.id, "name": "Andere", "text": "x"}])
        db.session.add(p)
        db.session.commit()
        pid = p.id
        accounts.delete(user, "admin", {}, admin=me)
        p = db.session.get(ResearchProject, pid)
        assert not p.shares and p.comments[0]["name"] == "" and p.comments[0]["user_id"] is None


@pytest.mark.parametrize("problem", ["connection", "json"])
def test_k6_orcid_unreachable_is_no_500(app, monkeypatch, problem):
    import requests

    from app import auth

    class NoJson:
        status_code = 200

        @staticmethod
        def json():
            raise ValueError("no JSON")

    def post(*a, **k):
        if problem == "connection":
            raise requests.ConnectionError("down")
        return NoJson

    app.config["ORCID_CLIENT_ID"] = "client"
    monkeypatch.setattr(auth.requests, "post", post)
    c = app.test_client()
    with c.session_transaction() as s:
        s["oauth_state"] = "abc"
    assert c.get("/auth/orcid/callback?state=abc&code=x").status_code == 502


def test_comment_names_go_by_the_account_not_only_by_its_name(app, client, survey):
    add(client, "info")
    other = colleague(app, "editor")
    theirs, mine = _comment(other, "Von der Kollegin"), _comment(client, "Von Philipp")
    other.post(f"{E__review2_reste}/comments/{mine}", data={"status": "resolved"})
    with app.app_context():
        me = db.session.scalar(db.select(User).filter_by(orcid="0000-0000-0000-001X"))
        them = db.session.scalar(db.select(User).filter_by(orcid=OTHER__review2_reste))
        assert db.session.get(PreviewComment, theirs).author_id == them.id
        assert db.session.get(PreviewComment, mine).handled_by_id == them.id
        # the person has another name by now; a comment with the old name that another account wrote stays
        them.name = "Neuer Name"
        db.session.add(PreviewComment(survey_id=1, block_id=2, text="Andere", author="Neuer Name", author_id=me.id,
                                      via="editor"))
        db.session.commit()
        accounts.delete(db.session.get(User, them.id), "self")
        rows = {c.text: (c.author, c.author_id, c.handled_by, c.handled_by_id)
                for c in db.session.scalars(db.select(PreviewComment))}
    assert rows["Von der Kollegin"] == ("", None, "", None)
    assert rows["Von Philipp"][0] == "Philipp" and rows["Von Philipp"][2:] == ("", None)
    assert rows["Andere"][:2] == ("Neuer Name", rows["Andere"][1]) and rows["Andere"][1] is not None


# --- from tests/audits/test_review_fixes.py: What the code review of 2026-10-02 found and what holds since (code-review-2026-10-02.md, S1 to S7). ---


def test_layout_drawn_under_the_signature_with_texts_stays_valid(app, client, survey):
    """Layouts of participations that started before this change carry the signature over the whole flow."""
    test_flow.setup_design(app, client)
    url = _start(app, client)
    p = app.test_client()
    p.get(url)
    p.post(url, data=CONSENT_OK)
    p.post(url, data={"nav": "next", "i2": "30"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        r = db.session.scalar(db.select(Response).filter_by(is_preview=False))
        r.layout = {**r.layout, "sig": runtime._signature_with_texts(s)}
        db.session.commit()
    before = _state(app)
    p.get(url)
    after = _state(app)
    with app.app_context():
        assert after["sig"] == runtime._signature(db.session.get(Survey, 1))  # today's signature from now on
    assert {**after, "sig": None} == {**before, "sig": None}


# --- from tests/audits/test_review_medium.py: The medium findings of the code review of 2026-10-02 (code-review-2026-10-02.md, M5 to M16). ---


def test_leaving_a_group_ends_the_shares_of_ones_surveys(app, client, survey):
    gid, mid = _group__review_medium(app, client)
    other = _as__review_medium(app, OTHER__review_medium, "Kollegin")
    assert other.get("/surveys/1").status_code == 200
    page = client.post(f"/groups/{gid}/members/{mid}/remove", follow_redirects=True).data.decode()
    assert "ist nicht mehr geteilt" in page and "Geteilt bleibt" not in page
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(SurveyGroup.id))) == 0
    assert other.get("/surveys/1").status_code == 404


def test_survey_stays_shared_while_another_admin_of_it_is_in_the_group(app, client, survey):
    gid, mid = _group__review_medium(app, client)
    with app.app_context():
        db.session.add(SurveyMember(survey_id=1, orcid=OTHER__review_medium, name="Kollegin", role="admin", position=1))
        db.session.commit()
    page = client.post(f"/groups/{gid}/members/{mid}/remove", follow_redirects=True).data.decode()
    assert "Geteilt bleibt „" in page
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(SurveyGroup.id))) == 1


def test_viewers_see_typed_answers_only_from_five_on(app, client, survey):
    add(client, "cloze")                                                          # 2
    client.post(E__review_medium + "/items/2", data={"code": "cl", "question": "Ergänzen Sie.", "text": "Ich arbeite bei [    ].",
                                      "answer_mode": "optional"})
    add(client, "association")                                                    # 3
    client.post(E__review_medium + "/items/3", data={"code": "as", "question": "Was fällt Ihnen ein?", "answer_mode": "optional"})
    _answer(app, {"2": ["firma mustermann gmbh aachen"]})
    _answer(app, {"2": ["zuhause"]}, n=5)
    owner = client.get("/surveys/1/results").data.decode()
    assert "firma mustermann gmbh aachen" in owner and "zuhause" in owner
    viewer = _member__review_medium(app, "viewer").get("/surveys/1/results").data.decode()
    assert "zuhause" in viewer and "firma mustermann" not in viewer
    assert "zeigt die Rolle „Ansehen“ nicht" in viewer


# --- from tests/audits/test_s9_one_source.py: What participants see comes from one source (UI/UX audit 2026-10-07, S9): the preview renders the templates of the ---


@pytest.mark.sqlite_foreign_keys_off  # goes down and up again: SQLite's batch mode (tests/conftest.py, B8)
def test_the_migration_cleans_drafts_only(app, client, survey):
    add(client, "federal_state")  # 2
    client.post("/surveys/new", data={"title": "Läuft", "languages": ["de", "en"], "default_language": "de"})  # 2
    with app.app_context():
        from app.items import get

        copies = {lang: {**get("federal_state").default_texts(lang), "question": "Wo wohnen Sie?"} for lang in ("de", "en")}
        draft = db.session.get(Item, 2)
        draft.texts = copy.deepcopy(copies)
        running = db.session.get(Survey, 2)
        block = running.blocks[-1]
        item = Item(type="federal_state", code="BL", config=get("federal_state").default_config(), texts=copy.deepcopy(copies))
        block.items.append(item)
        consent = running.consent_item
        consent.texts = {**consent.texts, "de": {**consent.texts["de"], "agree": "Fortsetzen"}}
        running.status = Survey.RUNNING
        db.session.commit()
        kept_id = item.id
        migrations._run(command.downgrade, "0016")
        migrations._run(command.upgrade)
        db.session.expire_all()
        assert db.session.get(Item, 2).texts == {"de": {"question": "Wo wohnen Sie?"}, "en": {"question": "Wo wohnen Sie?"}}
        assert db.session.get(Item, kept_id).texts == copies  # running: as participants saw it
        assert db.session.get(Survey, 2).consent_item.texts["de"]["agree"] == "Fortsetzen"
        assert db.session.get(Item, kept_id).t("en")["options"]["s9"] == copies["en"]["options"]["s9"]


def test_no_physical_sides_outside_the_frames_kept_left_to_right():
    css = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
    ltr = _ltr_classes()
    side = re.compile(r"(?<![-\w])(margin-left|margin-right|padding-left|padding-right|border-left|border-right|left|right|"
                      r"float|text-align)\s*:\s*([^;}]+)")
    found = []
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        selector = " ".join(selector.split())
        for prop, value in side.findall(body):
            if prop in ("float", "text-align") and not re.search(r"\b(left|right)\b", value):
                continue
            classes = re.findall(r"\.([\w-]+)", selector)
            if (selector.startswith("[dir=") or selector in PHYSICAL_OK
                    or any(c == k or c.startswith(k + "-") for c in classes for k in ltr)):
                continue
            found.append(f"{selector}: {prop}")
    assert not found, found
