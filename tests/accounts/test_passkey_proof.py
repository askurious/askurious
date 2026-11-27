"""A passkey as proof of the signed-in account (app/passkeys.py, stage 2, Ralf 2026-11-27): where a page asks for the
current password, a code from the authenticator app or a fresh sign-in, an account with a passkey confirms with it
("Mit Passkey bestätigen"). The proof belongs to the signed-in account and to one purpose; it never sets or changes a
password. The passkey is one in software (tests/helpers/soft_passkey.py)."""
import time

import pytest

from app import accounts, limits, passkeys, passwords, twofactor
from app.extensions import db
from app.models import User
from tests.conftest import may_sign_in
from tests.helpers.soft_passkey import SoftPasskey, add, confirm

ADMIN = "0000-0000-0000-001X"  # site admin (tests/conftest.py); `client` is signed in as it, like an ORCID sign-in
OTHER = "0000-0002-1825-0097"
PASSWORD = "ein ziemlich langes passwort 7"


@pytest.fixture(autouse=True)
def _clean(app):
    limits._fallback.clear()
    twofactor._tries.clear()
    yield
    twofactor._tries.clear()


def _user(orcid=ADMIN):
    return db.session.scalar(db.select(User).filter_by(orcid=orcid))


def _other(app, password=False):
    """A second account (no site admin) signed in like an ORCID sign-in, with a passkey; password: also a password."""
    may_sign_in(app, OTHER)
    c = app.test_client()
    c.get(f"/auth/dev-login?orcid={OTHER}&name=Andere")
    c.post("/terms/", data={"accept": "1"})
    key = SoftPasskey()
    assert add(c, key)[1].get_json()["ok"]
    if password:
        with app.app_context():
            user = _user(OTHER)
            passwords.login_row(user, create=True).email = "andere@example.org"
            passwords.set_password(user, PASSWORD)
            db.session.commit()
        c.get(f"/auth/dev-login?orcid={OTHER}&name=Andere")      # the new password ended the session
    return c, key


def _local(app):
    """An account with a password only (no ORCID), signed in with it, with a passkey."""
    with app.app_context():
        local = accounts.create_local("Lokal", "lokal@example.org", "", False, _user())
        passwords.set_password(local, PASSWORD)
        db.session.commit()
    c = app.test_client()
    c.post("/auth/sign-in", data={"email": "lokal@example.org", "password": PASSWORD})
    c.post("/terms/", data={"accept": "1"})
    key = SoftPasskey()
    assert add(c, key, current=PASSWORD)[1].get_json()["ok"]
    return c, key


def _stale(client):
    with client.session_transaction() as s:
        s["signed_in_at"] = time.time() - accounts.FRESH_SECONDS - 5


@pytest.fixture
def strict(app):
    app.config["ADMIN_CODE_AFTER_ORCID"] = True
    yield app
    app.config["ADMIN_CODE_AFTER_ORCID"] = False


# --- the administration ----------------------------------------------------------------------------------------------


def test_an_admin_after_orcid_confirms_with_a_passkey_instead_of_the_code(strict, client):
    assert "/account/second-factor/set-up" in client.get("/admin/").headers["Location"]  # no passkey: set up a factor
    key = SoftPasskey()
    assert add(client, key)[1].get_json()["ok"]
    asked = client.get("/admin/")
    assert "/account/second-factor/code" in asked.headers["Location"]     # a passkey: no factor has to be set up
    page = client.get(asked.headers["Location"]).data.decode()
    assert "Mit Passkey bestätigen" in page and 'name="code"' not in page and "passkey.js" in page
    done = confirm(client, key, "admin", next="/admin/").get_json()
    assert done == {"ok": True, "next": "/admin/"}
    assert client.get("/admin/", follow_redirects=True).status_code == 200
    with client:
        client.get("/")
        from flask_login import current_user

        assert twofactor.passkey_proved() and twofactor.admin_rights(current_user) is True
    with client.session_transaction() as s:
        s["totp_ok"]["at"] -= 13 * 3600                                   # the hours are over: asked again
        s.modified = True
    assert "/account/second-factor/code" in client.get("/admin/").headers["Location"]


def test_the_code_page_offers_both_with_a_factor_and_only_the_code_without_a_passkey(strict, client):
    with strict.app_context():
        from app import secrets_box

        passwords.login_row(_user(), create=True).totp_box = secrets_box.encrypt(twofactor.new_secret())
        db.session.commit()
    page = client.get("/account/second-factor/code").data.decode()
    assert 'name="code"' in page and "Mit Passkey bestätigen" not in page
    with strict.app_context():
        from app.models import Passkey

        db.session.add(Passkey(user_id=_user().id, credential_id="x", public_key="k", user_handle="h", name="Laptop"))
        db.session.commit()
    page = client.get("/account/second-factor/code").data.decode()
    assert 'name="code"' in page and "Mit Passkey bestätigen" in page


def test_a_fresh_proof_does_not_open_the_administration(strict, client):
    key = SoftPasskey()
    assert add(client, key)[1].get_json()["ok"]
    assert confirm(client, key, "fresh").get_json()["ok"]
    assert "/account/second-factor/code" in client.get("/admin/").headers["Location"]


# --- whose passkey, which purpose, which one-time value --------------------------------------------------------------


def test_a_passkey_of_another_account_is_refused(app, client, caplog):
    other, others_key = _other(app)
    assert add(client, SoftPasskey())[1].get_json()["ok"]
    with caplog.at_level("INFO", logger="askurious.security"):
        refused = confirm(client, others_key, "fresh")
    assert refused.status_code == 401 and refused.get_json()["ok"] is False
    assert '"event":"passkey_failed"' in caplog.text and '"reason":"other"' in caplog.text and '"kind":"fresh"' in caplog.text
    no_key = app.test_client()
    no_key.get("/auth/dev-login?orcid=0000-0003-1415-9269&name=Ohne")
    no_key.post("/terms/", data={"accept": "1"})
    assert confirm(no_key, others_key, "fresh").get_json()["ok"] is False  # an account without a passkey gets no value
    assert app.test_client().post("/account/passkeys/confirm/options", json={"purpose": "fresh"}).status_code in (302, 401)


def test_a_one_time_value_of_one_place_counts_at_no_other(app, client, caplog):
    key = SoftPasskey()
    assert add(client, key)[1].get_json()["ok"]
    with caplog.at_level("INFO", logger="askurious.security"):
        assert confirm(client, key, "fresh", sent="admin").status_code == 401
    assert '"reason":"purpose"' in caplog.text
    with client:
        client.get("/")
        assert not twofactor.passkey_proved()
    assert client.post("/account/passkeys/confirm/options", json={"purpose": "erfunden"}).get_json()["ok"] is False


def test_the_one_time_value_of_a_proof_counts_once_and_not_for_ever(app, client):
    key = SoftPasskey()
    assert add(client, key)[1].get_json()["ok"]
    options = client.post("/account/passkeys/confirm/options", json={"purpose": "fresh"}).get_json()["publicKey"]
    assert [c["type"] for c in options["allowCredentials"]] == ["public-key"] and options["userVerification"] == "required"
    answer = {"purpose": "fresh", "credential": key.get(options)}
    assert client.post("/account/passkeys/confirm", json=answer).get_json()["ok"]
    assert client.post("/account/passkeys/confirm", json=answer).status_code == 401          # used up
    client.post("/account/passkeys/confirm/options", json={"purpose": "fresh"})
    assert client.post("/account/passkeys/confirm", json=answer).status_code == 401          # fits no later value
    options = client.post("/account/passkeys/confirm/options", json={"purpose": "fresh"}).get_json()["publicKey"]
    with client.session_transaction() as s:
        s[passkeys.CONFIRM_KEY]["at"] -= passkeys.CHALLENGE_SECONDS + 1
        s.modified = True
    assert client.post("/account/passkeys/confirm", json={"purpose": "fresh", "credential": key.get(options)}).status_code == 401


@pytest.mark.parametrize("switches", [{"verified": False}, {"origin": "http://ask.localhost:5060"}])
def test_a_proof_needs_user_verification_and_the_main_address(app, client, switches):
    key = SoftPasskey()
    assert add(client, key)[1].get_json()["ok"]
    _stale(client)
    assert confirm(client, key, "fresh", **switches).status_code == 401
    with client.session_transaction() as s:
        assert time.time() - s["signed_in_at"] > accounts.FRESH_SECONDS


def test_wrong_proofs_count_toward_the_limit_per_network(app, client, monkeypatch):
    monkeypatch.setattr(limits, "_network", lambda: "198.51.100.9")
    app.config.update(LOGIN_LIMIT=2, LOGIN_WINDOW=60)
    key = SoftPasskey()
    assert add(client, key)[1].get_json()["ok"]
    for _ in range(3):
        assert confirm(client, key, "fresh").get_json()["ok"]             # right ones do not count
    for _ in range(2):
        assert confirm(client, key, "fresh", verified=False).status_code == 401
    assert confirm(client, key, "fresh").status_code == 429


# --- a fresh sign-in ---------------------------------------------------------------------------------------------------


def test_a_proof_stands_for_signing_in_again(app, client, caplog):
    other, _ = _other(app)
    with app.app_context():
        uid = _user(OTHER).id
    _stale(client)
    asked = client.post(f"/admin/accounts/{uid}", data={"action": "passkeys_remove"})
    assert "/auth/login" in asked.headers["Location"]                    # no passkey: sign in again, as before
    key = SoftPasskey()
    with client.session_transaction() as s:
        s["signed_in_at"] = time.time()
    assert add(client, key)[1].get_json()["ok"]
    _stale(client)
    asked = client.post(f"/admin/accounts/{uid}", data={"action": "passkeys_remove"})
    assert "/account/passkeys/confirm?next=" in asked.headers["Location"]
    page = client.get(asked.headers["Location"]).data.decode()
    assert "Mit Passkey bestätigen" in page and "Neu mit ORCID anmelden" in page
    with app.app_context():
        assert passkeys.count(_user(OTHER)) == 1                         # nothing done yet
    with caplog.at_level("INFO", logger="askurious.security"):
        assert confirm(client, key, "fresh", next=f"/admin/accounts/{uid}").get_json()["next"] == f"/admin/accounts/{uid}"
    assert '"event":"passkey_confirmed"' in caplog.text and '"kind":"fresh"' in caplog.text
    client.post(f"/admin/accounts/{uid}", data={"action": "passkeys_remove"})
    with app.app_context():
        assert passkeys.count(_user(OTHER)) == 0
    assert client.get("/account/passkeys/confirm?next=/account/").headers["Location"].endswith("/account/")  # fresh now


def test_a_passkey_never_makes_a_password(app):
    other, key = _other(app)
    _stale(other)
    assert confirm(other, key, "fresh").get_json()["ok"]                  # fresh by a passkey
    with other:
        other.get("/")
        assert accounts._fresh() and not accounts.fresh_for_password() and not accounts.orcid_fresh()
    page = other.post("/account/password", data={"email": "neu@example.org", "password": PASSWORD, "repeat": PASSWORD},
                      follow_redirects=True).data.decode()
    assert "liegt zu lange zurück" in page
    with app.app_context():
        assert not passwords.has_password(_user(OTHER))

    local, key = _local(app)
    assert confirm(local, key, "delete").get_json()["ok"]                 # a proof of any kind
    page = local.post("/account/password", data={"passkey_proof": "1", "password": PASSWORD + " neu",
                                                 "repeat": PASSWORD + " neu"}, follow_redirects=True).data.decode()
    assert "Das aktuelle Passwort stimmt nicht" in page                   # changing asks for the password, as before
    fresh = app.test_client()
    assert fresh.post("/auth/sign-in", data={"email": "lokal@example.org", "password": PASSWORD}).status_code == 302


def test_a_sign_in_with_orcid_forgets_what_a_passkey_made_fresh(app):
    other, key = _other(app)
    assert confirm(other, key, "fresh").get_json()["ok"]
    other.get(f"/auth/dev-login?orcid={OTHER}&name=Andere")
    with other:
        other.get("/")
        assert accounts.fresh_for_password()


# --- the forms that ask for the current password -----------------------------------------------------------------------


def test_deleting_the_account_with_a_passkey_in_place_of_the_password(app, client):
    local, key = _local(app)
    page = local.get("/account/delete").data.decode()
    assert "Mit Passkey bestätigen" in page and 'data-passkey-confirm="delete"' in page
    gone = local.post("/account/delete", data={"passkey_proof": "1"}, follow_redirects=True).data.decode()
    assert "gilt nicht mehr" in gone                                      # no proof given: nothing deleted, no wrong password
    with app.app_context():
        row = passwords.login_row(db.session.scalar(db.select(User).filter_by(name="Lokal")))
        assert row is not None and not row.failed_count
    assert confirm(local, key, "orcid_remove").get_json()["ok"]
    assert "gilt nicht mehr" in local.post("/account/delete", data={"passkey_proof": "1"}, follow_redirects=True).data.decode()
    assert confirm(local, key, "delete").get_json()["ok"]
    done = local.post("/account/delete", data={"passkey_proof": "1"}, follow_redirects=True).data.decode()
    assert "Ihr Konto wurde gelöscht" in done
    with app.app_context():
        assert db.session.scalar(db.select(User).filter_by(name="Lokal")) is None


def test_a_proof_is_taken_once_and_not_kept(app):
    local, key = _local(app)
    assert confirm(local, key, "delete").get_json()["ok"]
    with local.session_transaction() as s:
        s[passkeys.PROOF_KEY]["at"] -= passkeys.PROOF_SECONDS + 1
        s.modified = True
    assert "gilt nicht mehr" in local.post("/account/delete", data={"passkey_proof": "1"}, follow_redirects=True).data.decode()
    with local.session_transaction() as s:
        assert passkeys.PROOF_KEY not in s
    with app.app_context():
        assert db.session.scalar(db.select(User).filter_by(name="Lokal")) is not None


def test_an_account_without_a_password_deletes_after_a_fresh_proof_and_its_name(app):
    other, key = _other(app)
    _stale(other)
    page = other.get("/account/delete").data.decode()
    assert 'data-passkey-confirm="fresh"' in page and "Neu mit ORCID anmelden" in page
    assert "liegt zu lange zurück" in other.post("/account/delete", data={"name": "Andere"}, follow_redirects=True).data.decode()
    assert confirm(other, key, "fresh").get_json()["ok"]
    assert "Ihr Konto wurde gelöscht" in other.post("/account/delete", data={"name": "Andere"}, follow_redirects=True).data.decode()


def test_disconnecting_orcid_and_removing_the_password_with_a_proof(app):
    other, key = _other(app)
    assert confirm(other, key, "orcid_remove").get_json()["ok"]
    assert other.post("/account/orcid/remove", data={"passkey_proof": "1"}).status_code == 403  # E6: no password, ORCID stays
    assert confirm(other, key, "password_remove").get_json()["ok"]
    assert other.post("/account/password/remove", data={"passkey_proof": "1"}).status_code == 403

    other, key = _other(app, password=True)                               # ORCID, a password and a passkey
    page = other.get("/account/").data.decode()
    assert 'data-passkey-confirm="orcid_remove"' in page and 'data-passkey-confirm="password_remove"' in page
    assert confirm(other, key, "orcid_remove").get_json()["ok"]
    assert "gilt nicht mehr" in other.post("/account/password/remove", data={"passkey_proof": "1"},
                                           follow_redirects=True).data.decode()       # the proof of another place
    with app.app_context():
        assert passwords.has_password(_user(OTHER))
    assert confirm(other, key, "password_remove").get_json()["ok"]
    assert "ist entfernt" in other.post("/account/password/remove", data={"passkey_proof": "1"},
                                        follow_redirects=True).data.decode()
    with app.app_context():
        user = _user(OTHER)
        assert user.has_orcid and not passwords.has_password(user) and passkeys.count(user) == 2  # both stay


def test_disconnecting_orcid_with_a_proof_while_the_password_stays(app):
    other, key = _other(app, password=True)
    assert confirm(other, key, "orcid_remove").get_json()["ok"]
    assert "ist von Ihrem Konto getrennt" in other.post("/account/orcid/remove", data={"passkey_proof": "1"},
                                                        follow_redirects=True).data.decode()
    with app.app_context():
        user = db.session.scalar(db.select(User).filter_by(name="Andere"))
        assert not user.has_orcid and passwords.has_password(user)


def test_connecting_orcid_with_a_proof_in_place_of_password_and_code(app):
    app.config["ORCID_CLIENT_ID"] = "APP-TEST"
    local, key = _local(app)
    assert 'data-passkey-confirm="orcid_link"' in local.get("/account/").data.decode()
    refused = local.post("/auth/login", data={"link": "1", "fresh": "1", "passkey_proof": "1"})
    assert refused.headers["Location"].endswith("/account/#anmeldung")    # no proof: back, nothing begun
    with local.session_transaction() as s:
        assert "orcid_link" not in s
    assert confirm(local, key, "orcid_link").get_json()["ok"]
    begun = local.post("/auth/login", data={"link": "1", "fresh": "1", "passkey_proof": "1"})
    assert begun.headers["Location"].startswith(app.config["ORCID_BASE_URL"] + "/oauth/authorize")
    with local.session_transaction() as s:
        assert s["orcid_link"]["user"]


def test_a_further_passkey_with_a_proof_of_one_the_account_has(app):
    local, key = _local(app)
    assert 'data-passkey-confirm="add"' in local.get("/account/").data.decode()
    first = local.post("/account/passkeys/add/options", json={"name": "Zweiter", "proof": True}).get_json()
    assert first["ok"] is False and "gilt nicht mehr" in first["error"]
    assert confirm(local, key, "add").get_json()["ok"]
    options = local.post("/account/passkeys/add/options", json={"name": "Zweiter", "proof": True}).get_json()
    assert options["ok"]
    second = SoftPasskey()
    assert local.post("/account/passkeys/add", json={"credential": second.create(options["publicKey"])}).get_json()["ok"]
    with app.app_context():
        assert passkeys.count(db.session.scalar(db.select(User).filter_by(name="Lokal"))) == 2
