"""A registration with a password through an invitation link becomes an account only after mailbox proof
(app/signup_links.py, _create_with_password and confirm), with mail switched on: the form mails an encrypted
confirmation link and makes nothing, and the page says the same whether the address has an account or not. Before, the
holder of a link could register with someone else's address, and learn which addresses have an account (access audit
2026-11-11). With mail off the account is made at once, as before."""
import re

from app.extensions import db
from app.models import SignUpLink, UserLogin
from tests.accounts.test_invitations_page import _make, _org, _register
from tests.conftest import mail_on


def _link_in(mails, to):
    mail = [m for m in mails if m["To"] == to][-1]
    found = re.search(r"https?://[^\s]+/signup/confirm/[^\s]+", mail.get_content())
    return found.group(0).split("/signup/", 1)[1] if found else None


def test_the_account_comes_with_the_click_in_the_mail(app, client):
    sent = mail_on(app)
    token = _make(client, _org(app))
    page = _register(app, token)
    assert page.status_code == 302
    with app.app_context():
        assert db.session.scalar(db.select(UserLogin).filter_by(email="kim@example.org")) is None   # nothing yet
        assert db.session.scalar(db.select(SignUpLink)).uses == 0
    path = _link_in(sent, "kim@example.org")
    assert path
    guest = app.test_client()
    assert guest.get("/signup/" + path).status_code == 302
    with app.app_context():
        login = db.session.scalar(db.select(UserLogin).filter_by(email="kim@example.org"))
        assert login is not None and login.password_hash and login.email_verified_at is not None
        assert db.session.scalar(db.select(SignUpLink)).uses == 1
    again = app.test_client().get("/signup/" + path, follow_redirects=True).data.decode()
    assert "gilt nicht mehr" in again                                           # used once


def test_a_taken_address_gets_the_same_answer(app, client):
    sent = mail_on(app)
    org_id = _org(app)
    first = _make(client, org_id)
    _register(app, first)
    app.test_client().get("/signup/" + _link_in(sent, "kim@example.org"))      # Kim's account exists now
    second = _make(client, org_id, note="noch eine")
    free = _register(app, second, email="neu@example.org").headers["Location"]
    taken = _register(app, second).headers["Location"]
    assert free == taken                                                        # the same way on, whatever the address
    taken_mail = [m for m in sent if m["To"] == "kim@example.org"][-1]
    assert "schon registriert" in taken_mail["Subject"] and "/signup/confirm/" not in taken_mail.get_content()
