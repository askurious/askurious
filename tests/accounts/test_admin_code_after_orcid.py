"""Site admins give the code from the authenticator app also after an ORCID sign-in (app/twofactor.py, config
ADMIN_CODE_AFTER_ORCID, on by default; Ralf 2026-11-20 after the audit of that day: ORCID does not say whether a second
factor was used there). Admins of an organisation and all other accounts as before; the operator resets a lost factor
on the server (app/factor_reset.py). The other tests run with the switch off (tests/conftest.py)."""
import re
import time

import pytest

from app import factor_reset, limits, twofactor
from app.extensions import db
from app.models import User

ADMIN = "0000-0000-0000-001X"  # site admin (tests/conftest.py); the client signs in like an ORCID sign-in


@pytest.fixture
def strict(app):
    limits._fallback.clear()
    twofactor._tries.clear()
    app.config["ADMIN_CODE_AFTER_ORCID"] = True
    yield app
    app.config["ADMIN_CODE_AFTER_ORCID"] = False
    twofactor._tries.clear()


def _code(app, later=0):
    with app.app_context():
        admin = db.session.scalar(db.select(User).filter_by(orcid=ADMIN))
        return twofactor.code_at(twofactor.secret_of(admin), int(time.time() // twofactor.STEP) + later)


def _set_up(client):
    """The admin sets the factor up on the page the administration led to; the page with the backup codes follows."""
    page = client.get("/account/second-factor/set-up").data.decode()
    secret = re.search(r"<code>([A-Z2-7]{16,})</code>", page).group(1)
    code = twofactor.code_at(secret, int(time.time() // twofactor.STEP))
    done = client.post("/account/second-factor/set-up", data={"code": code}).data.decode()
    assert "Ersatzcodes" in done
    return done


def test_the_administration_asks_a_site_admin_for_the_code_after_orcid(strict, client):
    r = client.get("/admin/")                                    # signed in like an ORCID sign-in, no factor yet
    assert r.status_code == 302 and "/account/second-factor/set-up" in r.headers["Location"]
    _set_up(client)
    assert client.get("/admin/", follow_redirects=True).status_code == 200  # the setup counts as the code given
    with client.session_transaction() as s:
        s.pop("totp_ok", None)                                   # another browser, or the hours are over
    r = client.get("/admin/")
    assert r.status_code == 302 and "/account/second-factor/code" in r.headers["Location"]
    with strict.app_context(), strict.test_request_context():
        pass
    client.post("/account/second-factor/code", data={"code": _code(strict, later=1)})  # a later step: each counts once
    assert client.get("/admin/", follow_redirects=True).status_code == 200


def test_admin_rights_on_ordinary_pages_need_the_code_too(strict, client):
    with client.session_transaction() as s:
        s.pop("totp_ok", None)
    with client:
        client.get("/")
        from flask_login import current_user

        assert twofactor.orcid_exempt() is False
        assert twofactor.admin_rights(current_user) is False     # like any other account until the code is given


def test_with_the_switch_off_orcid_is_enough_as_before(app, client):
    assert app.config["ADMIN_CODE_AFTER_ORCID"] is False
    assert client.get("/admin/", follow_redirects=True).status_code == 200


def test_other_accounts_pass_with_orcid_as_before(strict):
    other = strict.test_client()
    other.get("/auth/dev-login?orcid=0000-0002-1825-0097&name=Andere")
    other.post("/terms/", data={"accept": "1"})
    with other:
        other.get("/")
        assert twofactor.orcid_exempt() is True                  # no site admin: the rule of 2026-10-13 holds


def test_the_operator_resets_a_lost_factor(strict, client):
    _set_up(client)
    with strict.app_context():
        assert factor_reset.reset("0000-0000-0000-0001") is None          # no such account
        assert factor_reset.reset(ADMIN) is True
        db.session.commit()
        admin = db.session.scalar(db.select(User).filter_by(orcid=ADMIN))
        assert not twofactor.enabled(admin) and twofactor.backup_left(admin) == 0
        assert factor_reset.reset(ADMIN) is False                          # nothing left to reset
    with client.session_transaction() as s:
        s.pop("totp_ok", None)
    r = client.get("/admin/")
    assert r.status_code == 302 and "/account/second-factor/set-up" in r.headers["Location"]
