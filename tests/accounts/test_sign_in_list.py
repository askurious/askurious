"""Who may sign in with ORCID by themselves, kept in Administration > Forschende (app/signin.py; Ralf, 2026-10-06):
the list of iDs and the switch "every ORCID holder" in place of ALLOWED_ORCIDS, which is taken over once at the
start of the server. Being on the list is about signing in; creating surveys is the tick on the account."""
from app import signin
from app.extensions import db
from app.models import SignInOrcid, SiteSetting, User, UserLogin

ME = "0000-0000-0000-001X"       # site admin (tests/conftest.py)
OTHER = "0000-0002-1825-0097"
THIRD = "0000-0000-0000-0124"
NEW = "0000-0002-0000-000X"


def _as(client, orcid, name):
    client.get(f"/auth/dev-login?orcid={orcid}&name={name}")
    client.post("/terms/", data={"accept": "1"})


def _may_create(app, orcid):
    """"Anlegen" for some survey type (app/kind_access.py), what the tick on the account was until 2026-10-18."""
    with app.app_context():
        login = db.session.get(UserLogin, db.session.scalar(db.select(User.id).filter_by(orcid=orcid)))
        return bool(login and login.kinds and "create" in login.kinds.values())


def _post(client, **data):
    return client.post("/admin/admit", data=data, follow_redirects=True).data.decode()


def test_the_configuration_is_taken_over_once(app):
    _as(app.test_client(), OTHER, "Kollegin")                                 # an account from before, no tick
    with app.app_context():
        db.session.execute(db.delete(SiteSetting))                            # as on a server before this version
        db.session.commit()
        app.config["ALLOWED_ORCIDS"] = [OTHER, f"https://orcid.org/{THIRD}", "kein-orcid"]
        assert signin.adopt_config() == 2
        assert sorted(db.session.scalars(db.select(SignInOrcid.orcid))) == sorted([OTHER, THIRD])
        assert not signin.everyone()
        app.config["ALLOWED_ORCIDS"] = [NEW]
        assert signin.adopt_config() is None                                  # once: later changes are not read
        assert not signin.listed(NEW)
    assert _may_create(app, OTHER)                                            # nobody loses what the list gave


def test_a_star_turns_the_switch_on_and_keeps_creating_for_everyone(app):
    _as(app.test_client(), OTHER, "Kollegin")
    with app.app_context():
        db.session.execute(db.delete(SiteSetting))
        db.session.commit()
        app.config["ALLOWED_ORCIDS"] = ["*"]
        assert signin.adopt_config() == 0
        assert signin.everyone()
    assert _may_create(app, OTHER)


def test_the_list_lets_in_and_taking_off_signs_out(app, client):
    other = app.test_client()
    _as(other, OTHER, "Kollegin")
    app.config["DEV_LOGIN"] = False                                           # as in production
    assert other.get("/dashboard/").status_code == 302                        # neither listed nor invited
    app.config["DEV_LOGIN"] = True
    assert "Freigeschaltet" in _post(client, action="add", orcid=f" https://orcid.org/{OTHER.lower()} ", note="Kollegin, RWTH")
    app.config["DEV_LOGIN"] = False
    assert other.get("/dashboard/").status_code == 200
    app.config["DEV_LOGIN"] = True
    page = client.get("/admin/people").data.decode()
    assert "Anmeldung freischalten" in page and "Kollegin, RWTH" in page
    assert f">{OTHER}<" not in page                                           # iDs as the ORCID symbol, never as text
    assert "Von der Liste entfernt" in _post(client, action="remove", orcid=OTHER)
    app.config["DEV_LOGIN"] = False
    assert other.get("/dashboard/").status_code == 302                        # with the next click
    app.config["DEV_LOGIN"] = True


def test_the_switch_lets_every_orcid_holder_in(app, client):
    other = app.test_client()
    _as(other, OTHER, "Kollegin")
    _post(client, action="everyone", value="1")
    app.config["DEV_LOGIN"] = False
    assert other.get("/dashboard/").status_code == 200
    app.config["DEV_LOGIN"] = True
    assert 'name="mode" value="all" checked' in client.get("/admin/people").data.decode()
    _post(client, action="everyone", value="0")
    app.config["DEV_LOGIN"] = False
    assert other.get("/dashboard/").status_code == 302
    app.config["DEV_LOGIN"] = True


def test_listing_an_account_lets_it_create_and_an_admin_takes_that_back(app, client):
    app.config["NEW_ACCOUNTS_MAY_CREATE"] = True
    other = app.test_client()
    _as(other, OTHER, "Kollegin")                                             # not let in by itself: no tick
    assert not _may_create(app, OTHER)
    assert "darf damit jetzt auch eigene Befragungen anlegen" in _post(client, action="add", orcid=OTHER)
    assert _may_create(app, OTHER)
    with app.app_context():
        uid = db.session.scalar(db.select(User.id).filter_by(orcid=OTHER))
    client.post(f"/admin/accounts/{uid}", data={"action": "may_create", "value": "0"})
    assert "page-head-actions" not in other.get("/dashboard/").data.decode()  # the list alone lets create nothing
    assert "schon freigeschaltet" in _post(client, action="add", orcid=OTHER)
    assert not _may_create(app, OTHER)                                        # adding again gives nothing back


def test_a_new_account_from_the_list_gets_the_tick(app, client):
    app.config["NEW_ACCOUNTS_MAY_CREATE"] = True
    _post(client, action="add", orcid=THIRD)
    _as(app.test_client(), THIRD, "Dritte")
    assert _may_create(app, THIRD)


def test_only_site_admins_change_the_list_and_bad_ids_are_refused(app, client):
    other = app.test_client()
    _as(other, OTHER, "Kollegin")
    assert other.post("/admin/admit", data={"action": "add", "orcid": THIRD}).status_code in (403, 404)
    # refused in the dialog "Person hinzufügen" (UI/UX audit 2026-10-07, S10): what was typed stays, the field is marked
    r = client.post("/admin/admit", data={"action": "add", "orcid": "0000-0000", "note": "Frau X"})
    page = r.data.decode()
    assert r.status_code == 422 and "keine gültige ORCID iD" in page and 'value="0000-0000"' in page
    assert 'value="Frau X"' in page and 'aria-invalid="true" aria-describedby="allow-orcid-error"' in page
    with app.app_context():
        assert not signin.listed(THIRD) and db.session.scalar(db.select(db.func.count()).select_from(SignInOrcid)) == 0


def test_the_configuration_left_over_is_named(app, client):
    app.config["ALLOWED_ORCIDS"] = [OTHER]
    assert "steht noch <code>ALLOWED_ORCIDS</code>" in client.get("/admin/people").data.decode()
    app.config["ALLOWED_ORCIDS"] = []
    assert "ALLOWED_ORCIDS</code>. Es wurde" not in client.get("/admin/people").data.decode()


def test_deleting_the_admin_who_listed_keeps_the_entry(app, client):
    from app import accounts

    _post(client, action="add", orcid=THIRD)
    with app.app_context():
        admin = db.session.scalar(db.select(User).filter_by(orcid=ME))
        accounts.delete(admin, by="admin")
        row = db.session.get(SignInOrcid, THIRD)
        assert row is not None and row.added_by_id is None


def test_a_yes_to_signing_in_is_kept_a_few_seconds_a_no_never(app, monkeypatch):
    """Code review 2026-10-15, H10: load_user asked up to four tables on every request; a yes is now kept
    SIGN_IN_SECONDS per process (as balance.cached), a no is asked again each time."""
    from app import auth

    asked = []
    answer = [True]
    monkeypatch.setattr(auth, "_may_sign_in", lambda orcid: asked.append(orcid) or answer[0])
    app.config["SIGN_IN_SECONDS"] = 10
    with app.app_context():
        assert auth._may_sign_in_kept(OTHER) and auth._may_sign_in_kept(OTHER)
        assert asked == [OTHER]                                              # the second from the store
        answer[0] = False
        assert not auth._may_sign_in_kept(THIRD) and not auth._may_sign_in_kept(THIRD)
        assert asked == [OTHER, THIRD, THIRD]                                # a no is asked each time
        app.config["SIGN_IN_SECONDS"] = 0
        assert not auth._may_sign_in_kept(OTHER)                             # 0: always asked


def test_the_hint_says_who_gets_in_without_the_list_and_until_when(app, client, survey):
    from app.models import SurveyMember

    _post(client, action="add", orcid=THIRD)
    page = client.get("/admin/people").data.decode()
    assert "kommt auch ohne Liste rein" not in page
    with app.app_context():
        db.session.add(SurveyMember(survey_id=1, orcid=THIRD, name="Dritte", role="editor", position=2))
        db.session.commit()
    page = client.get("/admin/people").data.decode()
    assert "kommt auch ohne Liste rein (Befragung, Gruppe oder Freigabe am Konto)" in page
    assert "Fällt das weg, kann sie sich nicht mehr anmelden." in page and "Gruppe oder Admin" not in page


def test_taking_the_last_right_away_ends_the_kept_yes_at_once(app, client, survey):
    """Audit 2026-10-18, AUTH-3: the kept yes held up to SIGN_IN_SECONDS after the person was taken out of the last
    survey or group. Deleting the entry (one by one or in bulk) or the line of the sign-in list forgets it in this
    process at once."""
    from app import auth
    from app.models import SurveyMember

    _as(app.test_client(), OTHER, "Kollegin")
    app.config["SIGN_IN_SECONDS"] = 600
    with app.app_context():
        uid = db.session.scalar(db.select(User.id).filter_by(orcid=OTHER))
        admin = db.session.scalar(db.select(User).filter_by(orcid=ME))
        assert not auth._may_sign_in_kept(OTHER)
        db.session.add(SurveyMember(survey_id=1, user_id=uid, role="viewer"))
        db.session.commit()
        assert auth._may_sign_in_kept(OTHER)
        db.session.delete(db.session.scalar(db.select(SurveyMember).filter_by(user_id=uid)))
        db.session.commit()
        assert not auth._may_sign_in_kept(OTHER)                              # the entry went: no kept yes
        db.session.add(SurveyMember(survey_id=1, user_id=uid, role="viewer"))
        db.session.commit()
        assert auth._may_sign_in_kept(OTHER)
        db.session.execute(db.delete(SurveyMember).where(SurveyMember.user_id == uid))
        db.session.commit()
        assert not auth._may_sign_in_kept(OTHER)                              # in bulk as well
        signin.add(OTHER, "", admin)
        assert auth._may_sign_in_kept(OTHER)
        signin.remove(OTHER)
        assert not auth._may_sign_in_kept(OTHER)                              # off the sign-in list
