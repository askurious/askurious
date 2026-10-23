"""Site admins sign in with ORCID, or with their password plus a code from an authenticator app such as 2FAS
(Ralf 2026-10-09; app/twofactor.py, auth.sign_in_code). A password alone never sets the factor up."""
import re
import time

import pytest

from app import limits, passwords, twofactor
from app.extensions import db
from app.models import User
from tests.accounts.test_passwords import GOOD

ADMIN = "0000-0000-0000-001X"  # site admin (tests/conftest.py)
MAIL = "admin@example.org"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    limits._fallback.clear()  # the counts per sender and account are in each test's own database (audit 2026-10-13)
    twofactor._tries.clear()
    yield
    twofactor._tries.clear()


def _admin_with_password(app, client):
    client.get(f"/auth/dev-login?orcid={ADMIN}&name=Ralf")
    client.post("/terms/", data={"accept": "1"})
    with app.app_context():
        admin = db.session.scalar(db.select(User).filter_by(orcid=ADMIN))
        passwords.login_row(admin, create=True).email = MAIL
        passwords.set_password(admin, GOOD)  # ends the browser's session (a new password signs out everywhere)
        db.session.commit()
    client.get(f"/auth/dev-login?orcid={ADMIN}&name=Ralf")


def _setup(client):
    page = client.get("/account/second-factor/set-up").data.decode()
    secret = re.search(r"Schlüssel: <code>([A-Z2-7]+)</code>", page).group(1)
    r = client.post("/account/second-factor/set-up", data={"code": twofactor.code_at(secret, int(time.time() // 30)),
                                                          "next": "/dashboard/"})
    codes = re.findall(r"<li><code>([a-z0-9]{4}-[a-z0-9]{4})</code></li>", r.data.decode())
    return secret, codes


def _now(secret, shift=0):
    return twofactor.code_at(secret, int(time.time() // 30) + shift)


def test_without_a_factor_the_password_alone_does_not_sign_an_admin_in(app, client):
    _admin_with_password(app, client)
    c = app.test_client()
    r = c.post("/auth/sign-in", data={"email": MAIL, "password": GOOD})
    assert r.status_code == 401 and "Authenticator-App" in r.data.decode()
    assert c.get("/account/second-factor/set-up").status_code in (302, 401)       # not signed in: no setup


@pytest.mark.parametrize("active", [True, False])
def test_orcid_admin_needs_no_extra_code_but_unknown_session_does(app, client, active):
    _admin_with_password(app, client)
    app.config["ADMIN_TOTP"] = active
    assert client.get("/admin/people").status_code == 200
    with client.session_transaction() as sess:
        sess.pop(twofactor.SIGNED_IN_VIA, None)
        sess["_user_id"] = ".".join(sess["_user_id"].split(".")[:3])             # older token with no origin proof
    response = client.get("/admin/people")
    assert response.status_code == 302 and "/account/second-factor/set-up" in response.headers["Location"]


def test_remembered_orcid_admin_keeps_the_exemption(app, client):
    _admin_with_password(app, client)
    client.get(f"/auth/dev-login?orcid={ADMIN}&remember=1")
    app.config["ADMIN_TOTP"] = True
    client.delete_cookie(app.config["SESSION_COOKIE_NAME"])
    assert client.get("/admin/people").status_code == 200
    with client.session_transaction() as sess:
        assert sess[twofactor.SIGNED_IN_VIA] == twofactor.VIA_ORCID


def test_password_and_code_sign_an_admin_in(app, client):
    _admin_with_password(app, client)
    secret, codes = _setup(client)                                                # ADMIN_TOTP off: still possible
    assert len(codes) == 8 and len(set(codes)) == 8
    app.config["ADMIN_TOTP"] = True
    c = app.test_client()
    r = c.post("/auth/sign-in", data={"email": MAIL, "password": GOOD, "next": "/admin/people"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/auth/sign-in/code")
    assert c.get("/dashboard/").status_code == 302                                # not signed in yet
    assert "Code stimmt nicht" in c.post("/auth/sign-in/code", data={"code": "000000"}, follow_redirects=True).data.decode()
    r = c.post("/auth/sign-in/code", data={"code": _now(secret, 1)})
    assert r.status_code == 302 and r.headers["Location"].endswith("/admin/people")
    assert c.get("/admin/people").status_code == 200                                    # no second code for the administration


def test_a_backup_code_works_once(app, client):
    _admin_with_password(app, client)
    secret, codes = _setup(client)
    for attempt, expected in ((codes[0], 302), (codes[0], 200)):
        c = app.test_client()
        c.post("/auth/sign-in", data={"email": MAIL, "password": GOOD})
        r = c.post("/auth/sign-in/code", data={"code": attempt.upper()})
        assert r.status_code == expected
    with app.app_context():
        assert twofactor.backup_left(db.session.scalar(db.select(User).filter_by(orcid=ADMIN))) == 7


def test_the_code_page_without_a_right_password_goes_back(app):
    c = app.test_client()
    r = c.get("/auth/sign-in/code")
    assert r.status_code == 302 and "/auth/sign-in" in r.headers["Location"]


def test_one_time_link_of_an_admin_without_a_factor_signs_nobody_in(app):
    # the link may come from "Passwort vergessen?" by mail: whoever reads the mailbox must not get past the factor,
    # nor set one up of their own (audit 2026-10-09b, T1); the factor is set up after a sign-in with ORCID
    with app.app_context():
        admin = User(orcid=ADMIN, name="Ralf")
        db.session.add(admin)
        db.session.flush()
        passwords.login_row(admin, create=True).email = MAIL
        token = passwords.new_link_token(admin, None, hours=passwords.RESET_HOURS)
        db.session.commit()
    c = app.test_client()
    r = c.post(f"/auth/password/{token}", data={"password": GOOD, "repeat": GOOD})
    assert r.status_code == 302 and r.headers["Location"].endswith("/auth/sign-in")
    assert c.get("/admin/ai").status_code == 302 and c.get("/dashboard/").status_code == 302   # not signed in
    assert c.get("/account/second-factor/set-up").status_code == 302
    with app.app_context():
        assert passwords.matches(db.session.scalar(db.select(User).filter_by(orcid=ADMIN)), GOOD)   # saved


def test_other_accounts_sign_in_with_the_password_as_before(app, client):
    from app import accounts
    from tests.conftest import allow

    with app.app_context():
        user = accounts.create_local("Gast", "gast@example.org", "", None, None)
        passwords.set_password(user, GOOD)
        db.session.commit()
    allow(app, "*")
    r = app.test_client().post("/auth/sign-in", data={"email": "gast@example.org", "password": GOOD})
    assert r.status_code == 302 and "/auth/sign-in/code" not in r.headers["Location"]


@pytest.mark.parametrize("change", ["password", "sign_out_everywhere", "remove_password"])
def test_a_changed_sign_in_between_password_and_code_ends_the_half_sign_in(app, client, change):
    """The half sign-in after the password (pw2fa) carries the account's session_version: a new or removed password
    or "sign out everywhere" in between sends the code back to the sign-in."""
    _admin_with_password(app, client)
    secret, _ = _setup(client)
    app.config["ADMIN_TOTP"] = True
    c = app.test_client()
    r = c.post("/auth/sign-in", data={"email": MAIL, "password": GOOD})
    assert r.headers["Location"].endswith("/auth/sign-in/code")
    if change == "sign_out_everywhere":
        assert client.post("/account/sign-out-everywhere").status_code == 302
    else:
        with app.app_context():
            admin = db.session.scalar(db.select(User).filter_by(orcid=ADMIN))
            if change == "password":
                passwords.set_password(admin, GOOD + " neu")
            else:
                passwords.remove(admin)
            db.session.commit()
    r = c.post("/auth/sign-in/code", data={"code": _now(secret, 1)})
    assert r.status_code == 302 and r.headers["Location"].endswith("/auth/sign-in")
    assert c.get("/dashboard/").status_code == 302                                     # not signed in
    with c.session_transaction() as sess:
        assert "pw2fa" not in sess


def test_the_code_after_an_unchanged_password_still_signs_in(app, client):
    """The counterpart: without a change in between, the code within PW2FA_SECONDS signs in as before."""
    _admin_with_password(app, client)
    secret, _ = _setup(client)
    app.config["ADMIN_TOTP"] = True
    c = app.test_client()
    c.post("/auth/sign-in", data={"email": MAIL, "password": GOOD})
    r = c.post("/auth/sign-in/code", data={"code": _now(secret, 1)})
    assert r.status_code == 302 and r.headers["Location"].endswith("/dashboard/")
    assert c.get("/dashboard/").status_code == 200


def test_a_one_time_link_of_an_admin_with_a_factor_leads_to_the_code(app, client):
    """The new password of a one-time link raises session_version; the half sign-in it leaves takes the version after
    that change, so the code signs in."""
    _admin_with_password(app, client)
    secret, _ = _setup(client)
    app.config["ADMIN_TOTP"] = True
    with app.app_context():
        admin = db.session.scalar(db.select(User).filter_by(orcid=ADMIN))
        token = passwords.new_link_token(admin, None, hours=passwords.RESET_HOURS)
        db.session.commit()
    c = app.test_client()
    new = GOOD + " neu"
    r = c.post(f"/auth/password/{token}", data={"password": new, "repeat": new})
    assert r.status_code == 302 and r.headers["Location"].endswith("/auth/sign-in/code")
    r = c.post("/auth/sign-in/code", data={"code": _now(secret, 1)})
    assert r.status_code == 302 and r.headers["Location"].endswith("/dashboard/")
