"""A pretest or phone link (/p/..., app/editor/share.py) ends with the sign-in of who made it: blocked, period over, a
new or removed password, "Überall abmelden". It carries the maker's session_version;
links from before carry none and count as version 0."""
import re
from datetime import timedelta

import pytest

from app import accounts, passwords
from app.extensions import db
from app.models import User, UserLogin, utcnow
from tests.conftest import add

MAKER = "0000-0000-0000-001X"  # the account of the client fixture
NEW_PASSWORD = "ein langer Satz als neues Passwort"


def _link(client):
    page = client.get("/surveys/1/field?pretest=7").data.decode()
    return re.search(r'id="pretest-url"[^>]*value="([^"]+)"', page).group(1).split("/p/", 1)[1]


def _maker(app):  # noqa: ARG001 - inside the caller's app context
    return db.session.scalar(db.select(User).filter_by(orcid=MAKER))


def _opens(tester, path):
    """(status of the page, status of a comment) over the link."""
    return (tester.get(f"/p/{path}").status_code,
            tester.post(f"/p/{path}/comment", data={"block_id": "2", "text": "Unklar"}).status_code)


def _block(app, client):
    with app.app_context():
        accounts.set_blocked(_maker(app), True, by=None)
        db.session.commit()


def _expire(app, client):
    with app.app_context():
        passwords.login_row(_maker(app), create=True).valid_until = utcnow() - timedelta(days=1)
        db.session.commit()


def _new_password(app, client):
    with app.app_context():
        passwords.set_password(_maker(app), NEW_PASSWORD)
        db.session.commit()


def _remove_password(app, client):
    with app.app_context():
        user = _maker(app)
        passwords.login_row(user, create=True).email = "maker@example.org"
        passwords.set_password(user, NEW_PASSWORD)
        db.session.commit()
    path = _link(_signed_in_again(app))  # the new password ended the client's session
    assert app.test_client().get(f"/p/{path}").status_code == 200
    with app.app_context():
        passwords.remove(_maker(app))
        db.session.commit()
    return path


def _signed_in_again(app):
    c = app.test_client()
    c.get(f"/auth/dev-login?orcid={MAKER}&name=Philipp")
    return c


def _sign_out_everywhere(app, client):
    assert client.post("/account/sign-out-everywhere").status_code == 302


@pytest.mark.parametrize("revoke", [_block, _expire, _new_password, _remove_password, _sign_out_everywhere],
                         ids=["blocked", "period_over", "new_password", "password_removed", "sign_out_everywhere"])
def test_a_pretest_link_ends_with_the_sign_in_of_its_maker(app, client, survey, revoke):
    add(client, "info")
    path = _link(client)
    tester = app.test_client()
    assert _opens(tester, path) == (200, 200)
    later = revoke(app, client)
    if later:  # removing a password needs one first: its link is made after that and worked until the removal
        path = later
    assert _opens(app.test_client(), path) == (404, 404)
    assert _opens(tester, path) == (404, 404)  # also in the browser that opened it before


def test_a_pretest_link_holds_while_the_sign_in_of_its_maker_does(app, client, survey):
    """The counterpart: a sign-in elsewhere or a new link changes nothing; a link made after a new password works."""
    add(client, "info")
    path = _link(client)
    _signed_in_again(app)
    _link(client)
    tester = app.test_client()
    assert _opens(tester, path) == (200, 200)
    _new_password(app, client)
    fresh = _link(_signed_in_again(app))
    assert _opens(tester, fresh) == (200, 200)
    assert tester.get(f"/p/{path}").status_code == 404


def test_a_link_from_before_holds_until_the_version_moves(app, client, survey):
    """Links made before the version was signed in carry no "v": they count as version 0, so they hold for an account
    whose sign-in never changed and end with the first change."""
    from app.editor.preview import _share_serializer, pretest_generation
    from app.models import Survey

    add(client, "info")
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        token = _share_serializer().dumps({"s": 1, "user": _maker(app).id, "d": 7, "g": pretest_generation(s)})
        assert int(db.session.scalar(db.select(UserLogin.session_version)
                                     .where(UserLogin.user_id == _maker(app).id)) or 0) == 0
    tester = app.test_client()
    assert tester.get(f"/p/{token}").status_code == 200
    _sign_out_everywhere(app, client)
    assert tester.get(f"/p/{token}").status_code == 404


def test_the_pretest_check_reads_only_columns_the_participants_role_may_read(app, client, survey):
    """user_login is open to the participants' own role for a few columns only (app/participant_grants.py)."""
    from tests.helpers import participant_sql

    add(client, "info")
    path = _link(client)
    tester = app.test_client()
    participant_sql.start()
    try:
        assert _opens(tester, path) == (200, 200)
        _block(app, client)
        assert tester.get(f"/p/{path}").status_code == 404
    finally:
        seen = participant_sql.stop()
    assert not participant_sql.violations(seen)
