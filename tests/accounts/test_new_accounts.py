"""New accounts may create surveys of their own (Ralf, 2026-10-05): the first sign-in and the admin form set the
tick, a site admin can take it back, and accounts from before keep what they had (NEW_ACCOUNTS_MAY_CREATE). The
first sign-in sets it only for iDs the configuration lets in, not for invited people (security audit 2026-10-06,
S11; tests/audits/test_audit_2026_10_06_d.py)."""
from app.extensions import db
from app.models import User, UserLogin
from tests.conftest import allow

GUEST = "0000-0002-1825-0097"


def _as(client, orcid, name):
    client.get(f"/auth/dev-login?orcid={orcid}&name={name}")
    client.post("/terms/", data={"accept": "1"})


def _uid(app, orcid=GUEST):
    with app.app_context():
        return db.session.scalar(db.select(User.id).filter_by(orcid=orcid))


def test_a_new_account_creates_surveys_until_an_admin_takes_it_back(app, client):
    app.config["NEW_ACCOUNTS_MAY_CREATE"] = True
    allow(app, GUEST)                                                         # let in by the configuration
    guest = app.test_client()
    _as(guest, GUEST, "Kollegin")
    allow(app)                                                                # the tick stays when the list changes
    assert "page-head-actions" in guest.get("/dashboard/").data.decode()
    uid = _uid(app)
    client.post(f"/admin/accounts/{uid}", data={"action": "may_create", "value": "0"})
    assert "page-head-actions" not in guest.get("/dashboard/").data.decode()
    _as(guest, GUEST, "Kollegin")                                             # signing in again gives nothing back
    assert "page-head-actions" not in guest.get("/dashboard/").data.decode()


def test_accounts_from_before_keep_what_they_had(app):
    guest = app.test_client()
    _as(guest, GUEST, "Kollegin")                                             # made while the setting was off
    app.config["NEW_ACCOUNTS_MAY_CREATE"] = True
    _as(guest, GUEST, "Kollegin")
    assert "page-head-actions" not in guest.get("/dashboard/").data.decode()
    with app.app_context():
        assert db.session.get(UserLogin, _uid(app)) is None


def test_the_admin_form_has_the_tick_set_for_new_accounts(app, client):
    tick = 'name="may_create" value="1" checked>'
    assert tick not in client.get("/admin/people").data.decode()
    app.config["NEW_ACCOUNTS_MAY_CREATE"] = True
    assert tick in client.get("/admin/people").data.decode()
