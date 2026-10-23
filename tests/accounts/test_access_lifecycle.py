"""Audit 2026-10-13, D: credentials expire with account authority; voluntary 2FA is enforced."""
import time
from datetime import timedelta

from app import accounts, auth, invites, passwords, sessions, twofactor
from app.extensions import db
from app.models import DataToken, MemberInvite, Survey, User, UserLogin, utcnow
from tests.conftest import allow
from tests.conftest import CREATE_ALL


def test_password_change_revokes_tokens_and_unused_recovery(app, client, survey):
    with app.app_context():
        user = db.session.get(User, 1)
        db.session.add(DataToken(user_id=user.id, survey_id=1, name="Script", token_hash="a" * 64,
                                 expires_at=utcnow() + timedelta(days=90)))
        token = passwords.new_link_token(user, None)
        db.session.commit()
        passwords.set_password(user, "A long unrelated sentence 7!")
        db.session.commit()
        assert db.session.scalar(db.select(DataToken)) is None
        assert passwords.link_row(token) is None


def test_emergency_sign_out_revokes_data_access(app, client, survey):
    with app.app_context():
        db.session.add(DataToken(user_id=1, survey_id=1, name="Script", token_hash="b" * 64,
                                 expires_at=utcnow() + timedelta(days=90)))
        db.session.commit()
    assert client.post("/account/sign-out-everywhere").status_code == 302
    with app.app_context():
        assert db.session.scalar(db.select(DataToken)) is None


def test_stale_session_cannot_create_data_access(app, client, survey):
    with client.session_transaction() as sess:
        sess["signed_in_at"] = time.time() - 3600
    response = client.post("/surveys/1/data-access/new", data={"name": "Hidden"})
    assert response.status_code == 302 and "/auth/" in response.location
    with app.app_context():
        assert db.session.scalar(db.select(DataToken)) is None


def test_invitation_rechecks_creator_authority(app, client, survey):
    with app.app_context():
        user, target = db.session.get(User, 1), db.session.get(Survey, 1)
        invite = MemberInvite(survey_id=1, email="guest@example.org", role="admin", created_by_id=1,
                              token_hash="c" * 64, expires_at=utcnow() + timedelta(days=1))
        db.session.add(invite)
        db.session.commit()
        assert invites.authorised(invite)
        target.members[0].role = "viewer"
        db.session.commit()
        assert not invites.authorised(invite)
        with app.test_request_context():
            goal, message = invites.join(invite, user=user)
        assert message and not any(m.role == "admin" for m in target.members)


def test_optional_factor_is_available_and_password_cannot_bypass_it(app, client):
    allow(app, "*")
    app.config["ADMIN_ORCIDS"] = set()
    app.config["ADMIN_TOTP"] = False
    assert client.get("/account/second-factor/set-up").status_code == 200
    with app.app_context():
        user = db.session.get(User, 1)
        row = passwords.login_row(user, create=True)
        row.email = "optional@example.org"
        passwords.set_password(user, "A long unrelated sentence 7!")
        from app import secrets_box
        row.totp_box = secrets_box.encrypt(twofactor.new_secret())
        db.session.commit()
        assert twofactor.required_at_signin(user)
    other = app.test_client()
    response = other.post("/auth/sign-in", data={"email": "optional@example.org", "password": "A long unrelated sentence 7!"})
    assert response.status_code == 302 and response.location.endswith("/auth/sign-in/code")
    assert other.get("/dashboard/").status_code == 302
    # Recovery does not bypass a voluntarily enabled factor either.
    with app.app_context():
        user = db.session.get(User, 1)
        token = passwords.new_link_token(user, None)
        db.session.commit()
    recovering = app.test_client()
    response = recovering.post(f"/auth/password/{token}", data={
        "password": "A different unrelated sentence 8!", "repeat": "A different unrelated sentence 8!"})
    assert response.status_code == 302 and response.location.endswith("/auth/sign-in/code")
    assert recovering.get("/dashboard/").status_code == 302


def test_ordinary_account_without_factor_can_sign_in(app, client):
    allow(app, "*")
    app.config["ADMIN_ORCIDS"] = set()
    with app.app_context():
        user = db.session.get(User, 1)
        row = passwords.login_row(user, create=True)
        row.email = "optional@example.org"
        passwords.set_password(user, "A long unrelated sentence 7!")
        db.session.commit()
        assert not twofactor.required_at_signin(user)
    other = app.test_client()
    response = other.post("/auth/sign-in", data={"email": "optional@example.org", "password": "A long unrelated sentence 7!"})
    assert response.status_code == 302 and not response.location.endswith("/code")
    other.post("/terms/", data={"accept": "1"})
    assert other.get("/dashboard/").status_code == 200
    assert not other.get_cookie(app.config["REMEMBER_COOKIE_NAME"])


def test_idle_session_expires_after_a_day(app, client):
    with client.session_transaction() as sess:
        sid = sess["sid"]
    with app.app_context():
        from app.models import LoginSession
        db.session.get(LoginSession, sid).last_seen_at = utcnow() - timedelta(hours=25)
        db.session.commit()
    assert client.get("/dashboard/").status_code == 302


def test_logout_removes_sensitive_session_state(app, client):
    with client.session_transaction() as sess:
        sess["admin_link"] = "private"
        sess["pw2fa"] = {"user": 1}
    client.post("/auth/logout")
    with client.session_transaction() as sess:
        assert not any(k in sess for k in ("admin_link", "pw2fa", "totp_ok", "signed_in_at", "signed_in_via", "csrf_token"))


def test_group_lead_can_invite_a_lead_without_platform_admin_rights(app, client):
    from app.models import WorkGroup, WorkGroupMember
    app.config["ADMIN_ORCIDS"] = set()
    with app.app_context():
        maker = db.session.get(User, 1)
        passwords.login_row(maker, create=True).kinds = CREATE_ALL
        group = WorkGroup(name="Team")
        group.members.append(WorkGroupMember(orcid=maker.orcid, user_id=maker.id, name=maker.name, role="lead"))
        db.session.add(group)
        db.session.flush()
        invite = MemberInvite(group_id=group.id, email="guest@example.org", role="lead", created_by_id=maker.id,
                              token_hash="d" * 64, expires_at=utcnow() + timedelta(days=1))
        db.session.add(invite)
        db.session.commit()
        assert invites.authorised(invite)
        group.members[0].role = "member"
        db.session.commit()
        assert not invites.authorised(invite)


def test_enabling_optional_factor_invalidates_old_password_cookies(app, client):
    from tests.accounts.test_twofactor import _setup, _now_code
    allow(app, "*")
    app.config["ADMIN_ORCIDS"] = set()
    with app.app_context():
        user = db.session.get(User, 1)
        row = passwords.login_row(user, create=True)
        row.email = "optional@example.org"
        passwords.set_password(user, "A long unrelated sentence 7!")
        db.session.commit()
    browser = app.test_client()
    browser.post("/auth/sign-in", data={"email": "optional@example.org", "password": "A long unrelated sentence 7!", "remember": "1"})
    stolen = app.test_client()
    for cookie in browser._cookies.values():
        stolen.set_cookie(cookie.key, cookie.value, domain=cookie.domain, path=cookie.path)
    assert stolen.get("/account/").status_code == 200
    secret = _setup(browser)
    browser.post("/account/second-factor/set-up", data={"code": _now_code(secret)})
    assert browser.get("/account/").status_code == 200
    assert stolen.get("/account/").status_code == 302


def test_invitation_can_only_be_claimed_once(app, client, survey):
    with app.app_context():
        maker = db.session.get(User, 1)
        invite = MemberInvite(survey_id=1, email="guest@example.org", role="viewer", created_by_id=1,
                              token_hash="e" * 64, expires_at=utcnow() + timedelta(days=1))
        db.session.add(invite)
        db.session.commit()
        with app.test_request_context():
            assert invites.join(invite, user=maker)[1] is None
            assert invites.join(invite, user=maker)[1] is not None
