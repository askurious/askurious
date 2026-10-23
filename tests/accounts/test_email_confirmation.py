"""AU-11: mailbox proof cannot reserve somebody else's address or activate another account."""
import re
from datetime import timedelta

from app import passwords
from app.extensions import db
from app.models import AuthToken, User, UserLogin, utcnow
from tests.conftest import mail_on
from tests.accounts.test_passwords import GOOD, ME, OTHER, _as


def begin(app, client, email="new@example.org"):
    sent = mail_on(app)
    client.post("/account/password", data={"email": email, "password": GOOD, "repeat": GOOD})
    assert sent
    return re.search(r"(/account/confirm-email/[\w-]+)", sent[-1].get_content())[1]


def test_confirmation_is_required_owner_bound_and_once_only(app, client):
    link = begin(app, client)
    with app.app_context():
        row = db.session.get(UserLogin, 1)
        assert row.email is None and row.password_hash is None
        assert db.session.scalar(db.select(AuthToken.secret_box)) and GOOD not in db.session.scalar(db.select(AuthToken.secret_box))
    other = app.test_client()
    _as(other, OTHER, "Other")
    assert other.get(link).status_code == 404
    assert client.get(link).status_code == 200  # mail scanners/GET never activate a password
    with app.app_context():
        assert db.session.get(UserLogin, 1).password_hash is None
    assert client.post(link).status_code == 302
    assert client.post(link).status_code == 404
    with app.app_context():
        row = db.session.get(UserLogin, 1)
        assert row.email == "new@example.org" and row.email_verified_at
        assert passwords.matches(db.session.get(User, 1), GOOD)


def test_pending_address_does_not_block_a_second_account_and_collision_keeps_owner(app, client):
    first = begin(app, client)
    other = app.test_client()
    _as(other, OTHER, "Other")
    second = begin(app, other)
    assert other.post(second).status_code == 302
    client.post(first)
    with app.app_context():
        assert db.session.get(UserLogin, 1).email is None
        owner = db.session.scalar(db.select(UserLogin).filter_by(email="new@example.org"))
        assert owner.user_id != 1


def test_expiry_revocation_and_mail_off_cannot_activate(app, client):
    link = begin(app, client)
    with app.app_context():
        db.session.scalar(db.select(AuthToken)).expires_at = utcnow() - timedelta(seconds=1)
        db.session.commit()
    assert client.post(link).status_code == 404
    link = begin(app, client)
    client.post("/account/sign-out-everywhere")
    assert client.post(link).status_code == 404
    app.config.update(MAIL_HOST="", MAIL_FROM="")
    client.post("/account/password", data={"email": "other@example.org", "password": GOOD, "repeat": GOOD})
    with app.app_context():
        assert db.session.get(UserLogin, 1).email is None


def test_existing_password_address_can_be_verified_without_replacing_password(app, client):
    with app.app_context():
        user = db.session.get(User, 1)
        passwords.login_row(user, create=True).email = "existing@example.org"
        passwords.set_password(user, GOOD)
        db.session.commit()
    _as(client, ME, "Admin")
    sent = mail_on(app)
    client.post("/account/verify-email")
    link = re.search(r"(/account/confirm-email/[\w-]+)", sent[-1].get_content())[1]
    with app.app_context():
        before = db.session.get(UserLogin, 1).password_hash
        version = db.session.get(UserLogin, 1).session_version
    assert client.post(link).status_code == 302
    with app.app_context():
        row = db.session.get(UserLogin, 1)
        assert row.email_verified_at and row.password_hash == before and row.session_version == version


def test_confirmation_requires_csrf_and_fresh_session(app, client):
    from flask.testing import FlaskClient

    link = begin(app, client)
    app.config["WTF_CSRF_ENABLED"] = True
    assert FlaskClient.post(client, link).status_code == 400
    app.config["WTF_CSRF_ENABLED"] = False
    with client.session_transaction() as session:
        session["signed_in_at"] -= 3600
    client.post(link)
    with app.app_context():
        assert db.session.get(UserLogin, 1).password_hash is None


def test_admin_address_change_invalidates_an_older_mailbox_proof(app, client):
    other = app.test_client()
    _as(other, OTHER, "Other")
    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.orcid == OTHER))
        user_id = user.id
        passwords.login_row(user, create=True).email = "old@example.org"
        passwords.set_password(user, GOOD)
        db.session.commit()
    _as(other, OTHER, "Other")
    sent = mail_on(app)
    other.post("/account/verify-email")
    link = re.search(r"(/account/confirm-email/[\w-]+)", sent[-1].get_content())[1]
    response = client.post(f"/admin/accounts/{user_id}", data={"action": "email", "email": "new@example.org"})
    assert response.status_code == 302
    assert other.post(link).status_code == 404
    with app.app_context():
        assert db.session.get(UserLogin, user_id).email == "new@example.org"


def test_confirmation_limit_is_persisted_for_the_account(app, client):
    from app.models import RateHit

    for n in range(3):
        begin(app, client, f"person{n}@example.org")
    response = client.post("/account/password", data={"email": "fourth@example.org", "password": GOOD, "repeat": GOOD},
                           follow_redirects=True)
    assert "warten Sie eine Stunde" in response.data.decode()
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(RateHit.id)).where(RateHit.kind == "email_confirm")) == 3

