"""Second factor for site admins (security audit 2026-10-04, H5): a code from an authenticator app before the
administration."""
import json
import re
import time

from app import twofactor
from app.extensions import db
from app.models import User, UserLogin
import pytest


@pytest.fixture(autouse=True)
def _no_tries_left_over():
    twofactor._tries.clear()  # counted per server process: one test's wrong codes must not pause the next
    yield
    twofactor._tries.clear()


def test_codes_follow_rfc_6238():
    # RFC 6238, appendix B (SHA-1, secret "12345678901234567890"), last six digits
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    for t, code in ((59, "287082"), (1111111109, "081804"), (1234567890, "005924"), (2000000000, "279037")):
        assert twofactor.code_at(secret, t // 30) == code
    assert twofactor.matching_step(secret, "287 082", now=59) == 1                 # blanks are fine
    assert twofactor.matching_step(secret, "287082", now=59 + 30) == 1             # one step late is accepted
    assert twofactor.matching_step(secret, "287082", now=59 + 90) is None          # too late
    assert twofactor.matching_step(secret, "287082", now=59, last=1) is None       # the same code twice: no


def _setup(client):
    page = client.get("/account/second-factor/set-up").data.decode()
    secret = re.search(r"Schlüssel: <code>([A-Z2-7]+)</code>", page).group(1)
    assert "otpauth" not in page and "<svg" in page                              # the QR code, drawn here
    return secret


def _now_code(secret, shift=0):
    return twofactor.code_at(secret, int(time.time() // 30) + shift)


def test_admin_needs_the_factor_for_the_administration(app, client):
    app.config["ADMIN_TOTP"] = True
    with client.session_transaction() as sess:
        sess[twofactor.SIGNED_IN_VIA] = twofactor.VIA_LINK
    r = client.get("/admin/people")
    assert r.status_code == 302 and "/account/second-factor/set-up" in r.headers["Location"]
    secret = _setup(client)
    r = client.post("/account/second-factor/set-up", data={"code": "000000", "next": "/admin/people"})
    assert "Code stimmt nicht" in client.get("/account/").data.decode() or r.status_code == 200
    r = client.post("/account/second-factor/set-up", data={"code": _now_code(secret), "next": "/admin/people"})
    assert r.status_code == 200 and 'href="/admin/people"' in r.data.decode()          # the backup codes, then on
    assert client.get("/admin/people").status_code == 200                              # this browser gave a code
    with app.app_context():
        row = db.session.get(UserLogin, db.session.scalar(db.select(User.id).filter_by(orcid="0000-0000-0000-001X")))
        assert row.totp_box and secret not in row.totp_box                       # kept encrypted
    # another browser of the same admin: asked for a code, which counts once
    other = app.test_client()
    other.get("/auth/dev-login?orcid=0000-0000-0000-001X&name=Philipp")
    other.post("/terms/", data={"accept": "1"})
    with other.session_transaction() as sess:
        sess[twofactor.SIGNED_IN_VIA] = twofactor.VIA_LINK
    r = other.get("/admin/people")
    assert "/account/second-factor/code" in r.headers["Location"]
    assert other.post("/account/second-factor/code", data={"code": _now_code(secret, -1), "next": "/admin/people"}).status_code in (200, 302)
    code = _now_code(secret, 1)
    assert other.post("/account/second-factor/code", data={"code": code, "next": "/admin/people"}).headers["Location"].endswith("/admin/people")
    assert other.get("/admin/people").status_code == 200
    third = app.test_client()
    third.get("/auth/dev-login?orcid=0000-0000-0000-001X&name=Philipp")
    with third.session_transaction() as sess:
        sess[twofactor.SIGNED_IN_VIA] = twofactor.VIA_LINK
    third.post("/account/second-factor/code", data={"code": code, "next": "/admin/people"})   # the same code again
    assert third.get("/admin/people").status_code == 302


def test_researchers_are_not_asked(app, client, survey):
    app.config["ADMIN_TOTP"] = True
    other = app.test_client()
    other.get("/auth/dev-login?orcid=0000-0002-1825-0097&name=Kollegin")
    other.post("/terms/", data={"accept": "1"})
    assert other.get("/dashboard/").status_code == 200
    assert client.get("/surveys/1").status_code == 200                           # the admin's own surveys: no code
    assert "Zweiter Faktor" in other.get("/account/").data.decode()                # offered to everyone


def test_wrong_codes_pause_and_switching_off_needs_a_code(app, client):
    app.config["ADMIN_TOTP"] = True
    secret = _setup(client)
    client.post("/account/second-factor/set-up", data={"code": _now_code(secret)})
    client.post("/account/second-factor/off", data={"code": "123456" if _now_code(secret) != "123456" else "654321"})
    with app.app_context():
        user = db.session.scalar(db.select(User).filter_by(orcid="0000-0000-0000-001X"))
        assert twofactor.enabled(user)
        twofactor._tries.clear()
        for _ in range(twofactor.TRIES):
            assert not twofactor.check(user, "000000")
        assert not twofactor.check(user, _now_code(secret, 1))                   # paused, even the right one
        twofactor._tries.clear()
    client.post("/account/second-factor/off", data={"code": _now_code(secret, 1)})   # a later step than the one used
    with app.app_context():
        assert not twofactor.enabled(db.session.scalar(db.select(User).filter_by(orcid="0000-0000-0000-001X")))


def test_switched_off_by_default_nothing_shows(app, client):
    from config import Config

    assert Config.ADMIN_TOTP is False                                            # built, not switched on (Ralf, 2026-10-04)
    app.config["ADMIN_TOTP"] = False
    assert client.get("/admin/people").status_code == 200                              # no code asked
    # site admins always see it: they need it to sign in with a password (Ralf 2026-10-09); everyone else does not
    assert "zweiter-faktor" in client.get("/account/").data.decode()
    from tests.conftest import allow

    allow(app, "*")
    other = app.test_client()
    other.get("/auth/dev-login?orcid=0000-0002-1825-0097&name=Kollegin")
    other.post("/terms/", data={"accept": "1"})
    assert "zweiter-faktor" in other.get("/account/").data.decode()              # voluntary setup for all accounts
    assert other.get("/account/second-factor/set-up").status_code == 200
    assert other.post("/account/second-factor/off", data={"code": "123456"}).status_code == 302


def test_new_backup_codes_need_a_current_code(app, client):
    """twofactor.backup_codes had no direct test (code review 2026-10-15, L8): a wrong code keeps the codes so far,
    a current one gives eight new ones (not cached) and ends the old ones; without a sign-in nothing."""
    app.config["ADMIN_TOTP"] = True
    secret = _setup(client)
    client.post("/account/second-factor/set-up", data={"code": _now_code(secret)})

    def stored():
        with app.app_context():
            return db.session.get(UserLogin, db.session.scalar(
                db.select(User.id).filter_by(orcid="0000-0000-0000-001X"))).totp_backup

    before = stored()
    assert before
    wrong = "123456" if _now_code(secret, 1) != "123456" else "654321"
    r = client.post("/account/second-factor/backup-codes", data={"code": wrong})
    assert r.status_code == 302 and r.headers["Location"].endswith("#zweiter-faktor")
    assert "die bisherigen Ersatzcodes bleiben gültig" in client.get("/account/").data.decode()
    assert stored() == before
    twofactor._tries.clear()
    r = client.post("/account/second-factor/backup-codes", data={"code": _now_code(secret, 1)})  # a step not used yet
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    assert stored() != before and len(json.loads(stored())) == 8
    assert app.test_client().post("/account/second-factor/backup-codes", data={"code": "000000"}).status_code in (302, 401, 404)
