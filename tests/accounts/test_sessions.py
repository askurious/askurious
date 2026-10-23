"""Sign-in sessions on the server (security audit 2026-10-04, H6, variant 2): "Abmelden" ends exactly this session,
copies of its cookies included; other devices stay signed in; the page Konto lists and ends sessions."""
from datetime import timedelta

from app import sessions
from app.extensions import db
from app.models import LoginSession, utcnow

ME = "0000-0000-0000-001X"
FIREFOX = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0"}


def _browser(app, headers=None):
    c = app.test_client()
    c.get(f"/auth/dev-login?orcid={ME}&name=Philipp", headers=headers or {})
    c.post("/terms/", data={"accept": "1"})
    return c


def _copy(app, source):
    """Another client holding copies of all cookies of `source` (what malware or a backup would take)."""
    c = app.test_client()
    for cookie in source._cookies.values():
        c.set_cookie(cookie.key, cookie.value, domain=cookie.domain, path=cookie.path)
    return c


def test_logout_ends_this_session_and_its_copies_only(app):
    laptop, phone = _browser(app, FIREFOX), _browser(app)
    stolen = _copy(app, laptop)
    assert stolen.get("/dashboard/").status_code == 200                         # the copy works while signed in
    laptop.post("/auth/logout")
    assert laptop.get("/dashboard/").status_code == 302
    assert stolen.get("/dashboard/").status_code == 302                         # the copy no longer signs in
    assert phone.get("/dashboard/").status_code == 200                          # the phone stays signed in


def test_account_page_lists_and_ends_sessions(app):
    laptop, phone = _browser(app, FIREFOX), _browser(app)
    page = phone.get("/account/").data.decode()
    assert "Firefox · Windows" in page and "dieser Browser" in page
    with app.app_context():
        rows = db.session.scalars(db.select(LoginSession).filter(LoginSession.ended_at.is_(None))).all()
        firefox = next(r.id for r in rows if r.label.startswith("Firefox"))
    phone.post(f"/account/sessions/{firefox}/end")
    assert laptop.get("/dashboard/").status_code == 302 and phone.get("/dashboard/").status_code == 200


def test_everywhere_and_new_password_close_the_rows(app):
    a, b = _browser(app), _browser(app)
    a.post("/account/sign-out-everywhere")
    assert b.get("/dashboard/").status_code == 302 and a.get("/dashboard/").status_code == 200
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(LoginSession.id)).filter(LoginSession.ended_at.is_(None))) == 1


def test_old_cookies_without_a_session_and_stale_sessions(app):
    c = _browser(app)
    with c.session_transaction() as sess:
        sess["_user_id"] = ".".join(sess["_user_id"].split(".")[:2])             # as written before the session list
    c.delete_cookie("remember_token")
    assert c.get("/dashboard/").status_code == 302
    d = _browser(app)
    with app.app_context():
        for row in db.session.scalars(db.select(LoginSession)):
            row.last_seen_at = utcnow() - timedelta(days=31)                    # not used for the cookie lifetime
        db.session.commit()
    assert d.get("/dashboard/").status_code == 302
    with app.app_context():
        assert sessions.sweep(utcnow()) >= 2


def test_account_page_shows_the_ten_most_recent_sessions(app):
    """V105: an account signed in sixty times does not list sixty rows; this browser is always among them."""
    first = _browser(app)
    for _ in range(12):
        _browser(app, FIREFOX)
    page = first.get("/account/").data.decode()
    table = page.split('class="list-table sessions-table"')[1].split("</table>")[0]
    assert table.count("<tr>") == 11 and "dieser Browser" in table            # head + 10 rows
    assert "und 3 ältere Sitzungen" in page and "Alle anderen Sitzungen beenden" in page
