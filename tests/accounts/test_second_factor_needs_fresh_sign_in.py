"""Setting up the second factor needs a fresh sign-in (app/twofactor.py, setup: accounts.need_fresh) and, with mail on,
tells the account's sign-in address: a session left open or taken over could set a factor of its own, lock the owner
out of signing in with the password and keep the backup codes, without the owner learning of it (access audit
2026-11-11, ASVS V7.5.1)."""
import re
import time

from app import twofactor
from app.extensions import db
from app.models import User, UserLogin
from tests.conftest import mail_on
from tests.accounts.test_twofactor import _now_code, _setup

ME = "0000-0000-0000-001X"


def test_an_old_session_signs_in_again_first(app, client):
    with client.session_transaction() as s:
        s["signed_in_at"] = time.time() - 24 * 3600
    r = client.get("/account/second-factor/set-up")
    assert r.status_code == 302 and r.headers["Location"].startswith("/auth/")  # the sign-in, back here afterwards
    assert "Schlüssel:" not in client.get("/account/second-factor/set-up").data.decode()  # still no secret shown


def test_a_fresh_session_sets_it_up_and_the_owner_hears_of_it(app, client):
    sent = mail_on(app)
    with app.app_context():
        uid = db.session.scalar(db.select(User.id).filter_by(orcid=ME))
        row = db.session.get(UserLogin, uid) or UserLogin(user_id=uid)
        row.email = "philipp@example.org"
        db.session.add(row)
        db.session.commit()
    secret = _setup(client)
    client.post("/account/second-factor/set-up", data={"code": _now_code(secret)})
    with app.app_context():
        assert twofactor.enabled(db.session.get(User, uid))
    mails = [m for m in sent if m["To"] == "philipp@example.org"]
    assert mails and "zweiter Faktor eingeschaltet" in mails[-1]["Subject"]
    assert re.search(r"Waren Sie\s+das nicht", mails[-1].get_content())
