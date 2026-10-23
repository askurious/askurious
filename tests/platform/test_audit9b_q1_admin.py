"""Audit 2026-10-09b, package Q1: the sign-in of site admins hardened (T1-T6, T9, V5).
Befunde: audit-sicherheit-performance-2026-10-09b-skripte/anmeldung/befunde.md."""
import hashlib
import json
import time

import pytest

from app import invites, passwords, twofactor
from app.extensions import db
from app.models import User, UserLogin
from tests.conftest import mail_on
from tests.accounts.test_admin_2fa import ADMIN, GOOD, MAIL, _admin_with_password, _clean, _now, _setup  # noqa: F401

WRONG = "000000"


def _wrong(secret):
    return WRONG if _now(secret) != WRONG and _now(secret, 1) != WRONG else "111111"


@pytest.fixture
def slack(monkeypatch):
    from app import notify

    said = []
    monkeypatch.setattr(notify, "slack", lambda text, blocks=None: said.append(text))
    return said


def _admin_row(app):
    with app.app_context():
        return db.session.get(UserLogin, db.session.scalar(db.select(User.id).filter_by(orcid=ADMIN)))


def _reset_link(app):
    with app.app_context():
        admin = db.session.scalar(db.select(User).filter_by(orcid=ADMIN))
        token = passwords.new_link_token(admin, None, hours=passwords.RESET_HOURS)
        db.session.commit()
    return token


def test_t1_reset_link_of_an_admin_leads_into_the_code_step(app, client, slack):
    _admin_with_password(app, client)
    secret, _codes = _setup(client)
    for totp in (False, True):
        app.config["ADMIN_TOTP"] = totp
        c = app.test_client()
        r = c.post(f"/auth/password/{_reset_link(app)}", data={"password": GOOD, "repeat": GOOD})
        assert r.status_code == 302 and r.headers["Location"].endswith("/auth/sign-in/code")
        assert c.get("/admin/ai").status_code != 200                                   # no admin without the code
        assert c.get("/dashboard/").status_code == 302                                 # not signed in at all
    assert any("Neues Passwort für ein Admin-Konto" in t for t in slack)              # T9
    assert c.post("/auth/sign-in/code", data={"code": _now(secret, 1)}).status_code == 302
    assert c.get("/admin/ai").status_code == 200                                       # with the code: in


def test_t2_wrong_codes_count_in_the_database_and_a_right_password_does_not_clear_them(app, client, slack):
    _admin_with_password(app, client)
    secret, _codes = _setup(client)
    c = app.test_client()
    for n in range(1, twofactor.CODE_FAILURES):
        twofactor._tries.clear()                                                       # as in another server process
        c.post("/auth/sign-in", data={"email": MAIL, "password": GOOD})                # the right password ...
        c.post("/auth/sign-in/code", data={"code": _wrong(secret)})
        assert _admin_row(app).failed_count == n * twofactor.code_weight()             # ... clears nothing
    assert len(slack) == 1 and "Passwort richtig, Code falsch" in slack[0]             # once an hour per account
    twofactor._tries.clear()
    c.post("/auth/sign-in", data={"email": MAIL, "password": GOOD})
    c.post("/auth/sign-in/code", data={"code": _wrong(secret)})
    row = _admin_row(app)
    assert row.locked_until is not None and row.failed_count == 0                      # the tenth: the account pauses
    twofactor._tries.clear()
    assert app.test_client().post("/auth/sign-in", data={"email": MAIL, "password": GOOD}).status_code == 401
    with app.app_context():
        admin = db.session.scalar(db.select(User).filter_by(orcid=ADMIN))
        assert not twofactor.check(admin, _now(secret, 1))                             # not even the right code
        passwords.set_password(admin, GOOD)                                            # a new password by mail ...
        db.session.commit()
    assert _admin_row(app).locked_until is not None                                    # ... clears no pause


def test_t2_the_right_code_resets_the_count(app, client):
    _admin_with_password(app, client)
    secret, _codes = _setup(client)
    c = app.test_client()
    c.post("/auth/sign-in", data={"email": MAIL, "password": GOOD})
    c.post("/auth/sign-in/code", data={"code": _wrong(secret)})
    assert _admin_row(app).failed_count == twofactor.code_weight()
    assert c.post("/auth/sign-in/code", data={"code": _now(secret, 1)}).status_code == 302
    assert _admin_row(app).failed_count == 0


def test_t2_other_accounts_keep_the_password_rules(app):
    # the decided pause after fifty wrong passwords (decisions.md, B13) and the reset by a right password stay
    from app import accounts

    assert twofactor.code_weight() * twofactor.CODE_FAILURES == passwords.ACCOUNT_FAILURES
    with app.test_request_context():
        user = accounts.create_local("Gast", "gast@example.org", "", None, None)
        passwords.set_password(user, GOOD)
        db.session.commit()
        passwords.login_row(user).failed_count = 7
        db.session.commit()
        assert passwords.confirm(user, GOOD) and passwords.login_row(user).failed_count == 0


def test_t3_a_password_session_that_becomes_admin_ends(app, client):
    from tests.conftest import allow

    other = "0000-0002-1825-0097"
    allow(app, other)
    c = app.test_client()
    c.get(f"/auth/dev-login?orcid={other}&name=Kollegin")
    with app.app_context():
        user = db.session.scalar(db.select(User).filter_by(orcid=other))
        passwords.login_row(user, create=True).email = "kollegin@example.org"
        passwords.set_password(user, GOOD)
        db.session.commit()
    c = app.test_client()
    c.post("/auth/sign-in", data={"email": "kollegin@example.org", "password": GOOD})
    c.post("/terms/", data={"accept": "1"})
    assert c.get("/dashboard/").status_code == 200
    with c.session_transaction() as s:
        assert s[twofactor.SIGNED_IN_VIA] == twofactor.VIA_PASSWORD
    admins = app.config["ADMIN_ORCIDS"]
    app.config["ADMIN_ORCIDS"] = set(admins) | {other}                                 # the operator makes it an admin
    try:
        assert c.get("/admin/ai").status_code != 200                                   # no admin rights without a code
        assert c.get("/account/second-factor/set-up").status_code == 302               # and no setup
        assert c.get("/dashboard/").status_code == 302                                 # the session has ended
    finally:
        app.config["ADMIN_ORCIDS"] = admins


def test_t3_admin_rights_and_setup_check_the_kind_of_sign_in(app, client):
    with app.test_request_context():
        from flask import session
        from flask_login import login_user

        admin = db.session.scalar(db.select(User).filter_by(orcid=ADMIN))
        login_user(admin)
        session[twofactor.SIGNED_IN_VIA] = twofactor.VIA_ORCID
        assert twofactor.admin_rights(admin)                                           # ORCID, ADMIN_TOTP off
        session[twofactor.SIGNED_IN_VIA] = twofactor.VIA_PASSWORD
        assert not twofactor.admin_rights(admin)
        r = twofactor.setup()
        assert r.status_code == 302 and "/account/" in r.headers["Location"]           # no factor from a password
        session[twofactor.SIGNED_IN_VIA] = twofactor.VIA_CODE
        assert not twofactor.admin_rights(admin)                                # a label alone is no code proof
        from app import secrets_box

        twofactor._row(admin, create=True).totp_box = secrets_box.encrypt(twofactor.new_secret())
        twofactor._passed()
        assert twofactor.admin_rights(admin)


def test_t4_a_code_taken_by_another_request_in_between_does_not_count(app, client):
    from sqlalchemy import update
    from sqlalchemy.orm.attributes import set_committed_value

    _admin_with_password(app, client)
    secret, codes = _setup(client)
    with app.app_context():
        admin = db.session.scalar(db.select(User).filter_by(orcid=ADMIN))
        row = passwords.login_row(admin)
        step = int(time.time() // 30) + 1
        kept = row.totp_backup
        # another request took the same code, committed after this one had read the row
        with db.engine.begin() as conn:
            conn.execute(update(UserLogin).where(UserLogin.user_id == admin.id).values(totp_step=step))
        assert not twofactor.check(admin, twofactor.code_at(secret, step))
        db.session.rollback()
        # and the first backup code
        with db.engine.begin() as conn:
            conn.execute(update(UserLogin).where(UserLogin.user_id == admin.id)
                         .values(totp_backup=json.dumps(json.loads(kept)[1:])))
        row = passwords.login_row(admin)
        set_committed_value(row, "totp_backup", kept)                                  # as read before the other wrote
        assert not twofactor.use_backup(admin, codes[0])
        db.session.rollback()
        twofactor._tries.clear()
        assert twofactor.use_backup(admin, codes[1])                                   # an unused one still works


def test_t5_backup_codes_are_salted_and_old_hashes_still_count(app, client):
    _admin_with_password(app, client)
    _secret, _codes = _setup(client)
    kept = json.loads(_admin_row(app).totp_backup)
    assert all(k.startswith("scrypt:") and "$" in k for k in kept)                    # salted, like passwords
    assert len({k.split("$")[1] for k in kept}) == len(kept)                           # a salt of its own each
    old = hashlib.sha256(b"abcd2345").hexdigest()                                      # stored before the change
    with app.app_context():
        admin = db.session.scalar(db.select(User).filter_by(orcid=ADMIN))
        passwords.login_row(admin).totp_backup = json.dumps([old] + kept[1:])
        db.session.commit()
        assert twofactor.use_backup(admin, "ABCD-2345")
        db.session.commit()
        assert twofactor.backup_left(admin) == 7
        twofactor.new_backup_codes(admin)                                              # new ones replace the old kind
        assert all(k.startswith("scrypt:") for k in json.loads(passwords.login_row(admin).totp_backup))


def test_t6_the_invitation_goes_into_the_session_only_for_the_orcid_way(app, client, survey):
    from app import secrets_box
    from tests.editor.test_invites import NEW, _token

    sent = mail_on(app)
    client.post(f"/invite/survey/{survey}", data={"email": NEW, "role": "editor"})
    token = _token(sent[-1])
    guest = app.test_client()
    r = guest.get(f"/invite/{token}")
    assert r.headers["Cache-Control"] == "no-store"                                    # T9
    with guest.session_transaction() as s:
        assert invites.SESSION_KEY not in s                                            # merely opened: nothing kept
    with app.test_request_context():
        from flask import session

        session[invites.SESSION_KEY] = {"box": secrets_box.encrypt(token), "at": time.time() - invites.SESSION_SECONDS - 1}
        assert invites.accept_after_orcid("0000-0000-0000-0124", "Dritte") is None     # too old
        assert invites.SESSION_KEY not in session
        session[invites.SESSION_KEY] = token                                           # the form of before: not taken
        assert invites.accept_after_orcid("0000-0000-0000-0124", "Dritte") is None


def test_t9_backup_code_and_switching_off_are_reported(app, client, slack):
    _admin_with_password(app, client)
    secret, codes = _setup(client)
    c = app.test_client()
    c.post("/auth/sign-in", data={"email": MAIL, "password": GOOD})
    assert c.post("/auth/sign-in/code", data={"code": codes[0]}).status_code == 302
    assert any("Ersatzcode benutzt" in t for t in slack)
    c.post("/account/second-factor/off", data={"code": _now(secret, 1)})
    assert any("Zweiter Faktor ausgeschaltet" in t for t in slack)


def test_v5_making_a_lead_needs_a_fresh_sign_in(app, client):
    from app.models import WorkGroup, WorkGroupMember

    client.post("/groups/new", data={"name": "Verwaist"})
    with app.app_context():
        gid = db.session.scalar(db.select(WorkGroup.id).order_by(WorkGroup.id.desc()))
        mid = db.session.scalar(db.select(WorkGroupMember.id).filter_by(group_id=gid))
    with client.session_transaction() as s:
        s["signed_in_at"] = 0
    r = client.post(f"/admin/groups/{gid}/lead", data={"member_id": mid})
    assert r.status_code == 302 and "/auth/login" in r.headers["Location"]
