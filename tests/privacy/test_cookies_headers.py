"""Security audit 2026-10-04: cookies with the prefix __Host- (N4), Zenodo only on Zenodo (N6), no-store for
signed-in pages (N7)."""
from datetime import date, timedelta

import pytest

HTTPS = "https://localhost"


def _secure(app):
    app.config.update(SESSION_COOKIE_NAME="__Host-session", SESSION_COOKIE_SECURE=True,
                      REMEMBER_COOKIE_NAME="__Host-remember", REMEMBER_COOKIE_SECURE=True)


def _cookie(response, name):
    return next((h for h in response.headers.getlist("Set-Cookie") if h.startswith(name + "=")), None)


def test_config_names_behind_https():
    import importlib
    import os

    import config

    old = os.environ.get("PUBLIC_BASE_URL")
    os.environ["PUBLIC_BASE_URL"] = "https://queryous.example"
    try:
        importlib.reload(config)
        assert config.Config.SESSION_COOKIE_NAME == "__Host-session"
        assert config.Config.REMEMBER_COOKIE_NAME == "__Host-remember"
    finally:
        if old is None:
            os.environ.pop("PUBLIC_BASE_URL")
        else:
            os.environ["PUBLIC_BASE_URL"] = old
        importlib.reload(config)
    assert config.Config.SESSION_COOKIE_NAME in ("session", "__Host-session")


def test_host_cookies_have_the_right_attributes(app):
    _secure(app)
    c = app.test_client()
    r = c.get("/auth/dev-login?orcid=0000-0000-0000-001X&name=Philipp&remember=1", base_url=HTTPS)
    session = _cookie(r, "__Host-session")
    remember = _cookie(r, "__Host-remember")
    for header in (session, remember):
        assert header and "Secure" in header and "Path=/" in header and "Domain=" not in header


def test_old_session_cookie_is_never_read_and_removed(app, client):
    # AU8 (audit 2026-10-11): the transition ended on 2026-10-12; a cookie under the old name could come from another
    # sub-domain, and no setting brings the old path back
    from flask.sessions import SecureCookieSessionInterface

    with app.test_request_context():
        old = SecureCookieSessionInterface().get_signing_serializer(app).dumps({"r1": 42, "_permanent": True})
    _secure(app)
    app.config["LEGACY_SESSION_COOKIE_UNTIL"] = (date.today() + timedelta(days=3)).isoformat()  # ignored
    q = app.test_client()
    q.set_cookie("session", old, domain="localhost")
    r = q.get("/imprint", base_url=HTTPS)
    assert _cookie(r, "session").startswith("session=;")  # the stale one is removed
    fresh = _cookie(r, "__Host-session")  # guest pages set a session of their own (CSRF token of the language form)
    if fresh:
        with app.test_request_context():
            assert "r1" not in app.session_interface._read(app, fresh.split(";")[0].split("=", 1)[1])


def test_zenodo_follows_addresses_on_zenodo_only(monkeypatch):
    from app import zenodo

    calls = []

    class R:
        status_code, content = 200, b"{}"

        def json(self):
            return {}

    monkeypatch.setattr(zenodo.requests, "request", lambda method, url, **kw: calls.append(url) or R())
    client = zenodo.Client("secret", "sandbox")
    client.request("GET", "/deposit/depositions")
    client.request("PUT", "https://sandbox.zenodo.org/api/files/b/data.csv")
    for url in ("https://evil.example/api/files/b/x", "http://sandbox.zenodo.org/api/files/b/x",
                "https://sandbox.zenodo.org.evil.example/x"):
        with pytest.raises(zenodo.ZenodoError):
            client.request("PUT", url)
    assert calls == ["https://sandbox.zenodo.org/api/deposit/depositions", "https://sandbox.zenodo.org/api/files/b/data.csv"]


def test_signed_in_pages_are_not_kept(app, client, survey):
    assert client.get("/surveys/1").headers["Cache-Control"] == "no-store"
    assert client.get("/dashboard/").headers["Cache-Control"] == "no-store"
    assert "no-store" not in (app.test_client().get("/imprint").headers.get("Cache-Control") or "")  # not signed in
    static = client.get("/static/app.css?v=x")
    assert "no-store" not in (static.headers.get("Cache-Control") or "")
