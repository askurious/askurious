"""Signing in with a passkey (app/passkeys.py, Ralf 2026-11-27): a third way in besides ORCID and e-mail with
password, added on "Konto > Profil und Anmeldung" with the proof connecting ORCID asks for; the device verifies the
person, so the sign-in counts like password and code, also for site admins. The passkey is one in software
(tests/helpers/soft_passkey.py)."""
import time

import pytest

from app import accounts, limits, passkeys, passwords, twofactor
from app.extensions import db
from app.models import Passkey, User
from tests.conftest import mail_on, may_sign_in
from tests.helpers.soft_passkey import SoftPasskey, add, sign_in

ADMIN = "0000-0000-0000-001X"  # site admin (tests/conftest.py); `client` is signed in as it, like an ORCID sign-in
OTHER = "0000-0002-1825-0097"
PASSWORD = "ein ziemlich langes passwort 7"


@pytest.fixture(autouse=True)
def _clean(app):
    limits._fallback.clear()
    twofactor._tries.clear()
    yield
    twofactor._tries.clear()


def _user(app, orcid=ADMIN):
    return db.session.scalar(db.select(User).filter_by(orcid=orcid))


def _other(app, name="Andere"):
    """A second account (no site admin) that may sign in, signed in like an ORCID sign-in."""
    may_sign_in(app, OTHER)
    c = app.test_client()
    c.get(f"/auth/dev-login?orcid={OTHER}&name={name}")
    c.post("/terms/", data={"accept": "1"})
    return c


def _with_password(app, orcid=OTHER, email="andere@example.org"):
    with app.app_context():
        user = _user(app, orcid)
        passwords.login_row(user, create=True).email = email
        passwords.set_password(user, PASSWORD)
        db.session.commit()


def _rows(app, orcid=ADMIN):
    with app.app_context():
        return passkeys.of(_user(app, orcid))


def test_a_passkey_is_added_listed_and_signs_in(app, client):
    key = SoftPasskey()
    first, second = add(client, key, name="  Mein   Laptop ")
    options = first.get_json()["publicKey"]
    assert options["rp"]["id"] == "localhost" and options["attestation"] == "none"
    assert options["authenticatorSelection"] == {"residentKey": "required", "requireResidentKey": True,
                                                 "userVerification": "required"}
    assert second.get_json()["ok"] and second.headers["Cache-Control"] == "no-store"
    row, = _rows(app)
    assert row.name == "Mein Laptop" and row.backed_up is False and row.last_used_at is None
    assert len(passkeys._decode(row.user_handle)) == 32          # random, neither the number nor an address
    page = client.get("/account/").data.decode()
    assert "Mein Laptop" in page and "nur auf diesem Gerät" in page and "noch nie" in page

    fresh = app.test_client()
    asked = fresh.post("/account/passkeys/sign-in/options", json={}).get_json()["publicKey"]
    assert asked["userVerification"] == "required" and asked["rpId"] == "localhost"
    assert "allowCredentials" not in asked                       # no account named: the device offers its passkeys
    done = fresh.post("/account/passkeys/sign-in", json={"credential": key.get(asked), "next": "/account/"})
    assert done.get_json() == {"ok": True, "next": "/account/"}
    with fresh.session_transaction() as s:
        assert s[twofactor.SIGNED_IN_VIA] == twofactor.VIA_PASSKEY
        assert time.time() - s["signed_in_at"] < 60              # counts as a fresh sign-in
        assert passkeys.SIGN_IN_KEY not in s
    assert fresh.get("/account/").status_code == 200
    row, = _rows(app)
    assert row.sign_count == 1 and row.last_used_at is not None


def test_a_site_admin_reaches_the_administration_with_a_passkey_and_no_code(app, client):
    app.config["ADMIN_CODE_AFTER_ORCID"] = True
    try:
        key = SoftPasskey()
        assert add(client, key)[1].get_json()["ok"]
        assert "/account/second-factor/code" in client.get("/admin/").headers["Location"]  # after ORCID: a proof
        fresh = app.test_client()
        assert sign_in(fresh, key).get_json()["ok"]
        assert fresh.get("/admin/", follow_redirects=True).status_code == 200  # no factor set up, none asked
        with fresh:
            fresh.get("/")
            from flask_login import current_user

            assert twofactor.by_passkey() and twofactor.admin_rights(current_user) is True
    finally:
        app.config["ADMIN_CODE_AFTER_ORCID"] = False


def test_an_account_with_a_second_factor_stays_signed_in_with_a_passkey(app):
    other = _other(app)
    key = SoftPasskey()
    assert add(other, key)[1].get_json()["ok"]
    with app.app_context():
        from app import secrets_box

        passwords.login_row(_user(app, OTHER), create=True).totp_box = secrets_box.encrypt(twofactor.new_secret())
        db.session.commit()
    fresh = app.test_client()
    assert sign_in(fresh, key).get_json()["ok"]
    assert fresh.get("/account/").status_code == 200             # auth.load_user: like password and code


@pytest.mark.parametrize("switches, why", [
    ({"origin": "http://ask.localhost:5060"}, "invalid"),        # the participants' address is another origin
    ({"origin": "https://localhost.example.org"}, "invalid"),
    ({"verified": False}, "invalid"),                            # the device did not verify the person
])
def test_a_wrong_origin_or_no_user_verification_is_refused(app, client, switches, why, caplog):
    key = SoftPasskey()
    assert add(client, key)[1].get_json()["ok"]
    fresh = app.test_client()
    with caplog.at_level("INFO", logger="askurious.security"):
        refused = sign_in(fresh, key, **switches)
    assert refused.status_code == 401 and refused.get_json()["ok"] is False
    assert f'"event":"passkey_failed"' in caplog.text and f'"reason":"{why}"' in caplog.text
    assert fresh.get("/account/").status_code == 302             # not signed in


def test_the_one_time_value_counts_once_and_not_for_ever(app, client):
    key = SoftPasskey()
    assert add(client, key)[1].get_json()["ok"]
    fresh = app.test_client()
    options = fresh.post("/account/passkeys/sign-in/options", json={}).get_json()["publicKey"]
    answer = key.get(options)
    assert fresh.post("/account/passkeys/sign-in", json={"credential": answer}).get_json()["ok"]
    again = app.test_client()
    again.post("/account/passkeys/sign-in/options", json={})     # another one-time value: the old answer fits none
    assert again.post("/account/passkeys/sign-in", json={"credential": answer}).status_code == 401
    assert fresh.post("/account/passkeys/sign-in", json={"credential": answer}).status_code == 401  # used up

    late = app.test_client()
    options = late.post("/account/passkeys/sign-in/options", json={}).get_json()["publicKey"]
    with late.session_transaction() as s:
        s[passkeys.SIGN_IN_KEY]["at"] -= passkeys.CHALLENGE_SECONDS + 1
        s.modified = True
    assert late.post("/account/passkeys/sign-in", json={"credential": key.get(options)}).status_code == 401


def test_an_unknown_passkey_gets_the_same_answer(app, client):
    key = SoftPasskey()
    assert add(client, key)[1].get_json()["ok"]
    fresh = app.test_client()
    wrong = sign_in(fresh, key, verified=False)
    unknown = sign_in(fresh, SoftPasskey())
    assert unknown.status_code == wrong.status_code == 401 and unknown.get_json() == wrong.get_json()


def test_a_blocked_account_does_not_get_in(app, client):
    other = _other(app)
    key = SoftPasskey()
    assert add(other, key)[1].get_json()["ok"]
    with app.app_context():
        accounts.set_blocked(_user(app, OTHER), True, _user(app))
        db.session.commit()
    refused = sign_in(app.test_client(), key)
    assert refused.status_code == 403 and "gesperrt" in refused.get_json()["error"]
    assert len(_rows(app, OTHER)) == 1                           # the passkey stays for when the block ends


def test_an_account_not_allowed_to_sign_in_does_not_get_in(app, client):
    other = app.test_client()
    other.get(f"/auth/dev-login?orcid={OTHER}&name=Andere")       # the local sign-in asks no list
    other.post("/terms/", data={"accept": "1"})
    key = SoftPasskey()
    assert add(other, key)[1].get_json()["ok"]
    refused = sign_in(app.test_client(), key)
    assert refused.status_code == 403 and "freigeschaltet" in refused.get_json()["error"]


def test_the_limit_per_network_holds_for_passkeys(app, client, monkeypatch):
    monkeypatch.setattr(limits, "_network", lambda: "198.51.100.7")  # one sender; counted in the database
    app.config.update(LOGIN_LIMIT=2, LOGIN_WINDOW=60)
    fresh = app.test_client()
    for _ in range(2):
        assert sign_in(fresh, SoftPasskey()).status_code == 401
    assert sign_in(fresh, SoftPasskey()).status_code == 429


def test_at_most_ten_passkeys_per_account(app, client):
    for i in range(passkeys.MAX_PER_ACCOUNT):
        assert add(client, SoftPasskey(), name=f"Gerät {i}")[1].get_json()["ok"]
    first, second = add(client, SoftPasskey())
    assert second is None and "höchstens 10" in first.get_json()["error"]
    assert len(_rows(app)) == passkeys.MAX_PER_ACCOUNT
    assert "Passkey hinzufügen …" not in client.get("/account/").data.decode()


def test_the_same_passkey_is_not_added_twice(app, client):
    key = SoftPasskey()
    first, second = add(client, key)
    assert second.get_json()["ok"]
    first, second = add(client, key, name="Noch einmal")
    assert [c["type"] for c in first.get_json()["publicKey"]["excludeCredentials"]] == ["public-key"]
    assert "schon hinterlegt" in second.get_json()["error"] and len(_rows(app)) == 1


def test_adding_needs_a_name_and_the_account_s_proof(app):
    other = _other(app)
    assert "Namen" in add(other, SoftPasskey(), name=" ")[0].get_json()["error"]
    with other.session_transaction() as s:
        s["signed_in_at"] = time.time() - accounts.FRESH_SECONDS - 5
    assert "zu lange zurück" in add(other, SoftPasskey())[0].get_json()["error"]   # no password: a fresh sign-in

    _with_password(app)
    other = _other(app)                                          # the new password ended the session
    with other.session_transaction() as s:
        s["signed_in_at"] = time.time() - accounts.FRESH_SECONDS - 5
    assert "Passwort stimmt nicht" in add(other, SoftPasskey(), current="falsch")[0].get_json()["error"]
    assert add(other, SoftPasskey(), current=PASSWORD)[1].get_json()["ok"]         # the password, fresh or not

    with app.app_context():
        from app import secrets_box

        secret = twofactor.new_secret()
        passwords.login_row(_user(app, OTHER)).totp_box = secrets_box.encrypt(secret)
        db.session.commit()
    assert "Code stimmt nicht" in add(other, SoftPasskey(), current=PASSWORD, code="000000")[0].get_json()["error"]
    code = twofactor.code_at(secret, int(time.time() // twofactor.STEP))
    assert add(other, SoftPasskey(), current=PASSWORD, code=code)[1].get_json()["ok"]
    assert len(_rows(app, OTHER)) == 2


def test_the_answer_of_adding_belongs_to_its_one_time_value(app, client):
    key = SoftPasskey()
    options = client.post("/account/passkeys/add/options", json={"name": "Laptop"}).get_json()["publicKey"]
    for wrong in (key.create(options, origin="http://ask.localhost:5060"), key.create(options, verified=False)):
        client.post("/account/passkeys/add/options", json={"name": "Laptop"})
        with client.session_transaction() as s:
            s[passkeys.ADD_KEY]["c"] = options["challenge"]
            s.modified = True
        assert client.post("/account/passkeys/add", json={"credential": wrong}).get_json()["ok"] is False
    answer = key.create(options)
    assert client.post("/account/passkeys/add", json={"credential": answer}).get_json()["ok"] is False  # value used up
    assert _rows(app) == []
    assert app.test_client().post("/account/passkeys/add/options", json={"name": "x"}).status_code in (302, 401)


def test_renaming_and_removing_only_one_s_own(app, client):
    assert add(client, SoftPasskey())[1].get_json()["ok"]
    row, = _rows(app)
    other = _other(app)
    assert other.post(f"/account/passkeys/{row.id}/rename", data={"name": "Fremd"}).status_code == 404
    assert other.post(f"/account/passkeys/{row.id}/remove").status_code == 404
    client.post(f"/account/passkeys/{row.id}/rename", data={"name": "Schlüssel am Bund"})
    assert _rows(app)[0].name == "Schlüssel am Bund"
    client.post(f"/account/passkeys/{row.id}/rename", data={"name": "  "})
    assert _rows(app)[0].name == "Schlüssel am Bund"
    page = client.post(f"/account/passkeys/{row.id}/remove", follow_redirects=True).data.decode()
    assert "Passkey entfernt" in page and _rows(app) == []


def test_a_removed_passkey_signs_nobody_in(app, client):
    key = SoftPasskey()
    assert add(client, key)[1].get_json()["ok"]
    client.post(f"/account/passkeys/{_rows(app)[0].id}/remove")
    assert sign_in(app.test_client(), key).status_code == 401


def test_a_passkey_is_never_the_other_way_in(app):
    """E6: ORCID or the password stays. The rules that keep the last way in do not count a passkey."""
    other = _other(app)
    assert add(other, SoftPasskey())[1].get_json()["ok"]
    assert other.post("/account/password/remove", data={"current": PASSWORD}).status_code == 403  # no password at all
    assert other.post("/account/orcid/remove", data={"current": PASSWORD}).status_code == 403     # ORCID is the only way
    with app.app_context():
        user = _user(app, OTHER)
        assert user.has_orcid and not passwords.has_password(user) and passkeys.count(user) == 1

    # an account with a password only: the passkey does not let it give the password up
    with app.app_context():
        admin = _user(app)
        local = accounts.create_local("Lokal", "lokal@example.org", "", False, admin)
        passwords.set_password(local, PASSWORD)
        db.session.commit()
    c = app.test_client()
    c.post("/auth/sign-in", data={"email": "lokal@example.org", "password": PASSWORD})
    c.post("/terms/", data={"accept": "1"})
    assert add(c, SoftPasskey(), current=PASSWORD)[1].get_json()["ok"]
    assert c.post("/account/password/remove", data={"current": PASSWORD}).status_code == 403
    with app.app_context():
        assert passwords.has_password(db.session.scalar(db.select(User).filter_by(name="Lokal")))


def test_a_new_password_and_signing_out_everywhere_leave_passkeys(app):
    _other(app)
    _with_password(app)
    other = _other(app)                                          # the new password ended the session
    key = SoftPasskey()
    assert add(other, key, current=PASSWORD)[1].get_json()["ok"]
    other.post("/account/password", data={"current": PASSWORD, "password": PASSWORD + " neu", "repeat": PASSWORD + " neu"})
    other.post("/account/sign-out-everywhere")
    assert len(_rows(app, OTHER)) == 1
    assert sign_in(app.test_client(), key).get_json()["ok"]


def test_a_site_admin_removes_all_passkeys_of_an_account(app, client):
    other = _other(app)
    keys = [SoftPasskey(), SoftPasskey()]
    for key in keys:
        assert add(other, key)[1].get_json()["ok"]
    with app.app_context():
        uid = _user(app, OTHER).id
    page = client.get(f"/admin/accounts/{uid}").data.decode()
    assert "Alle Passkeys entfernen" in page and "2 Passkeys" in page
    assert other.post(f"/admin/accounts/{uid}", data={"action": "passkeys_remove"}).status_code == 403  # admins only
    with client.session_transaction() as s:
        s["signed_in_at"] = time.time() - accounts.FRESH_SECONDS - 5
    client.post(f"/admin/accounts/{uid}", data={"action": "passkeys_remove"})
    assert len(_rows(app, OTHER)) == 2                           # a session left open is not enough
    with client.session_transaction() as s:
        s["signed_in_at"] = time.time()
    done = client.post(f"/admin/accounts/{uid}", data={"action": "passkeys_remove"}, follow_redirects=True).data.decode()
    assert "2 Passkeys von" in done and _rows(app, OTHER) == []
    assert "Alle Passkeys entfernen" not in done
    assert sign_in(app.test_client(), keys[0]).status_code == 401


def test_deleting_the_account_takes_its_passkeys_along(app, client):
    other = _other(app)
    assert add(other, SoftPasskey())[1].get_json()["ok"]
    with app.app_context():
        accounts.delete(_user(app, OTHER), "admin", {}, admin=_user(app))
        assert db.session.scalar(db.select(db.func.count(Passkey.id))) == 0


def test_a_synchronised_passkey_and_a_counter_that_goes_back(app, client, caplog):
    synced, single = SoftPasskey(synced=True), SoftPasskey()
    assert add(client, synced, name="Schlüsselbund")[1].get_json()["ok"]
    assert add(client, single, name="Stick")[1].get_json()["ok"]
    assert "synchronisiert" in client.get("/account/").data.decode()
    with caplog.at_level("INFO", logger="askurious.security"):
        for _ in range(2):                                       # a synchronised one always says 0: no finding
            assert sign_in(app.test_client(), synced).get_json()["ok"]
        assert sign_in(app.test_client(), single, counter=7).get_json()["ok"]
    assert "passkey_counter" not in caplog.text
    with caplog.at_level("INFO", logger="askurious.security"):
        assert sign_in(app.test_client(), single, counter=3).get_json()["ok"]  # noted, the sign-in goes on
    assert '"event":"passkey_counter"' in caplog.text
    by_name = {row.name: row for row in _rows(app)}
    assert by_name["Stick"].sign_count == 7 and by_name["Schlüsselbund"].sign_count == 0
    assert '"event":"sign_in"' in caplog.text and '"via":"passkey"' in caplog.text


def test_mail_and_security_log_about_added_and_removed_passkeys(app, caplog):
    _other(app)
    _with_password(app)
    other = _other(app)
    sent = mail_on(app)
    with caplog.at_level("INFO", logger="askurious.security"):
        assert add(other, SoftPasskey(), current=PASSWORD)[1].get_json()["ok"]
        assert sent[-1]["To"] == "andere@example.org" and "Passkey hinzugefügt" in sent[-1]["Subject"]
        other.post(f"/account/passkeys/{_rows(app, OTHER)[0].id}/remove")
    assert "Passkey entfernt" in sent[-1]["Subject"]
    assert '"event":"passkey_added"' in caplog.text and '"event":"passkey_removed"' in caplog.text
    assert "andere@example.org" not in caplog.text


def test_the_sign_in_page_offers_three_ways_with_orcid_first(app):
    """Ralf 2026-11-27: all three ways are complete, ORCID comes first with the only filled button and one sentence
    on what it gives; never "empfohlen". Then the passkey, then e-mail with password, each with the outlined button."""
    app.config["ORCID_CLIENT_ID"] = "APP-TEST"
    page = app.test_client().get("/auth/sign-in").data.decode()
    body = page.split('class="sign-in"')[1].split("</main>")[0]
    assert "Mit Passkey anmelden" in body and "passkey.js" in page
    for word in ("empfohlen", "recommended", "bevorzugt", "preferred"):
        assert word not in body
    assert body.index("Mit ORCID</h2>") < body.index("Mit Passkey</h2>") < body.index("Mit E-Mail-Adresse und Passwort</h2>")
    assert body.count('class="button"') == 1 and body.count('class="button button-secondary"') == 2
    assert body.index('class="button"') < body.index("Mit ORCID anmelden") < body.index('class="button button-secondary"')
    assert "Kein eigenes Passwort nötig" in body

    app.config["ORCID_CLIENT_ID"] = ""
    body = app.test_client().get("/auth/sign-in").data.decode().split('class="sign-in"')[1].split("</main>")[0]
    assert "Mit ORCID" not in body and body.count('class="button"') == 2   # without ORCID no way stands back
    assert 'autocomplete="username webauthn"' not in page        # no suggestion in the address field


def test_the_browser_may_ask_for_passkeys_on_researchers_pages_only(app, client, survey):
    policy = client.get("/account/").headers["Permissions-Policy"]
    assert "publickey-credentials-get=(self)" in policy and "publickey-credentials-create=(self)" in policy
    policy = app.test_client().get("/auth/sign-in").headers["Permissions-Policy"]
    assert "publickey-credentials-get=(self)" in policy
    with app.app_context():
        from app.models import Survey

        code = db.session.get(Survey, survey).code if isinstance(survey, int) else survey.code
    policy = app.test_client().get(f"/s/{code}").headers["Permissions-Policy"]
    assert "publickey-credentials-get=()" in policy and "publickey-credentials-create=()" in policy
