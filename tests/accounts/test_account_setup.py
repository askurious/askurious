"""One account, two ways to sign in, and the setup assistant after the first sign-in (konto-einrichtung-konzept.md,
all as recommended, Ralf 2026-10-18): new accounts are led once through four steps that can each be skipped,
accounts from before never see them; the sign-in with e-mail and password can be removed while ORCID stays."""
from app.extensions import db
from app.models import User
from tests.conftest import allow

GUEST = "0000-0002-1825-0097"
PASSWORD = "A long unrelated sentence 7!"


def _new(app, orcid=GUEST):
    allow(app, orcid)
    guest = app.test_client()
    guest.get(f"/auth/dev-login?orcid={orcid}&name=Kollegin")
    guest.post("/terms/", data={"accept": "1"})
    return guest


def _user(app, orcid=GUEST):
    with app.app_context():
        return db.session.scalar(db.select(User).filter_by(orcid=orcid))


def test_a_new_account_goes_through_the_steps_and_then_where_it_was_going(app):
    app.config["ACCOUNT_SETUP"] = True
    guest = _new(app)
    page = guest.get("/account/ai")
    assert page.status_code == 302 and page.headers["Location"].endswith("/account/setup/details")
    assert "Schritt 1 von 4" in guest.get("/account/setup/details").data.decode()
    assert guest.post("/account/setup/details", data={"lang": "en"}).headers["Location"].endswith("/account/setup/sign_in")
    assert guest.post("/account/setup/sign_in", data={"skip": "1"}).headers["Location"].endswith("/account/setup/ai")
    assert guest.post("/account/setup/ai").headers["Location"].endswith("/account/setup/learning")
    done = guest.post("/account/setup/learning", data={})                      # learning aids switched off
    assert done.headers["Location"].endswith("/account/ai")                    # where it was going before
    user = _user(app)
    assert not user.setup_due and not user.learning_aids and user.display["lang"] == "en"
    assert guest.get("/dashboard/").status_code == 200


def test_ending_the_assistant_ends_it_for_good(app):
    app.config["ACCOUNT_SETUP"] = True
    guest = _new(app)
    assert guest.get("/dashboard/").headers["Location"].endswith("/account/setup/details")
    assert guest.post("/account/setup/end").headers["Location"].endswith("/dashboard/")
    assert guest.get("/dashboard/").status_code == 200
    assert guest.get("/account/setup/details").headers["Location"].endswith("/dashboard/")


def test_accounts_from_before_never_see_it(app, client):
    app.config["ACCOUNT_SETUP"] = True
    with app.app_context():
        me = db.session.scalar(db.select(User).filter_by(orcid="0000-0000-0000-001X"))
        me.setup_due = False                                                   # as migration 0082 leaves them
        db.session.commit()
    assert client.get("/dashboard/").status_code == 200
    assert "Einrichtung erneut" not in client.get("/account/").data.decode()  # no second run (Ralf, 2026-10-18)


def test_an_account_without_orcid_names_itself_in_the_first_step(app):
    app.config["ACCOUNT_SETUP"] = True
    from app import accounts, passwords

    with app.app_context():
        user = accounts.create_local("", "gast@example.org", "", False, None)
        passwords.set_password(user, PASSWORD)
        db.session.commit()
        uid = user.id
    guest = app.test_client()
    guest.post("/auth/sign-in", data={"email": "gast@example.org", "password": PASSWORD})
    guest.post("/terms/", data={"accept": "1"})
    assert guest.post("/account/setup/details", data={"name": " "}).headers["Location"].endswith("/account/setup/details")
    guest.post("/account/setup/details", data={"name": "Gast  Person"})
    page = guest.get("/account/setup/sign_in").data.decode()
    assert "Mit ORCID verbinden" in page
    with app.app_context():
        assert db.session.get(User, uid).name == "Gast Person"


def test_the_ai_access_form_comes_back_to_its_step(app):
    app.config["ACCOUNT_SETUP"] = True
    guest = _new(app)
    guest.get("/account/setup/ai")
    back = guest.post("/account/ai/save", data={"provider": "nope"})
    assert back.headers["Location"].endswith("/account/setup/ai")


def test_the_password_goes_while_orcid_stays(app, client):
    from app import passwords

    with app.app_context():
        me = db.session.scalar(db.select(User).filter_by(orcid="0000-0000-0000-001X"))
        passwords.login_row(me, create=True).email = "philipp@example.org"
        passwords.set_password(me, PASSWORD)
        db.session.commit()
        uid = me.id
    client.get("/auth/dev-login?orcid=0000-0000-0000-001X&name=Philipp")       # set_password ended the session
    page = client.get("/account/").data.decode()
    assert 'id="anmeldung"' in page and "E-Mail-Adresse und Passwort entfernen" in page
    client.post("/account/password/remove", data={"current": "falsch"})
    with app.app_context():
        assert passwords.has_password(db.session.get(User, uid))
    client.post("/account/password/remove", data={"current": PASSWORD})
    with app.app_context():
        user = db.session.get(User, uid)
        assert not passwords.has_password(user) and passwords.login_row(user).email is None
    assert app.test_client().post("/auth/sign-in", data={"email": "philipp@example.org", "password": PASSWORD}).status_code == 401
    assert "nicht hinterlegt" in client.get("/account/").data.decode()


def test_an_account_without_orcid_keeps_its_password(app):
    from app import accounts, passwords

    with app.app_context():
        user = accounts.create_local("Gast", "gast@example.org", "", False, None)
        passwords.set_password(user, PASSWORD)
        db.session.commit()
    guest = app.test_client()
    guest.post("/auth/sign-in", data={"email": "gast@example.org", "password": PASSWORD})
    guest.post("/terms/", data={"accept": "1"})
    assert guest.post("/account/password/remove", data={"current": PASSWORD}).status_code == 403
    assert "E-Mail-Adresse und Passwort entfernen" not in guest.get("/account/").data.decode()
