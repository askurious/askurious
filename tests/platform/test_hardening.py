import pytest

from app.links import local_path
from tests.conftest import allow


def test_local_path_refuses_other_sites():
    for ok in ("/", "/surveys/1/data", "/surveys/1/data?x=1#zenodo"):
        assert local_path(ok) == ok
    for bad in ("", None, "//evil.example", "/\\evil.example", "/\\/evil.example", "https://evil.example",
                "javascript:alert(1)", "surveys/1", "/a\nb", "/\tevil.example"):
        assert local_path(bad) == ""


def test_next_parameters_stay_on_the_site(app, client, survey):
    r = client.post("/terms/", data={"accept": "1", "next": "/\\evil.example"})
    assert r.headers["Location"].endswith("/dashboard/")
    r = client.post("/zenodo/disconnect/sandbox", data={"next": "/\\evil.example"})
    assert r.headers["Location"].endswith("/dashboard/")
    html = client.get("/s/1/privacy?back=/\\evil.example").data.decode()
    assert "evil.example" not in html


def test_security_headers(app, client):
    h = client.get("/").headers
    # the whole policy: tests/audits/test_audit_small3.py (test_content_policy)
    assert h["X-Frame-Options"] == "SAMEORIGIN" and "frame-ancestors 'self'" in h["Content-Security-Policy"].split("; ")
    assert h["X-Content-Type-Options"] == "nosniff" and h["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "Strict-Transport-Security" not in h                                   # plain http (local development)
    assert "max-age" in client.get("/", base_url="https://queryous.example").headers["Strict-Transport-Security"]


def test_remember_cookie_settings(app):
    from datetime import timedelta

    assert app.config["REMEMBER_COOKIE_SAMESITE"] == "Lax" and app.config["REMEMBER_COOKIE_HTTPONLY"]
    assert app.config["REMEMBER_COOKIE_DURATION"] == timedelta(days=30)
    assert app.config["REMEMBER_COOKIE_SECURE"] == app.config["SESSION_COOKIE_SECURE"]


def test_refuses_to_start_behind_https_with_dev_login():
    from app import create_app
    from config import Config

    class Live(Config):
        PUBLIC_BASE_URL = "https://queryous.example"
        SECRET_KEY = "a-real-secret"
        DEV_LOGIN = True

    with pytest.raises(RuntimeError, match="DEV_LOGIN"):
        create_app(Live)


def test_removed_orcid_is_signed_out(app, survey):
    other = app.test_client()
    other.get("/auth/dev-login?orcid=0000-0001-5555-5555&name=Gast")
    other.post("/terms/", data={"accept": "1"})
    assert other.get("/dashboard/").status_code == 200
    app.config["DEV_LOGIN"] = False                                               # as in production
    assert other.get("/dashboard/").status_code == 302                            # neither listed nor invited
    allow(app, "0000-0001-5555-5555")
    assert other.get("/dashboard/").status_code == 200


def test_cron_key_in_header_or_query(app, client):
    assert client.get("/cron/close", headers={"X-Cron-Key": "k"}).status_code == 200
    assert client.get("/cron/close?key=k").status_code == 200
    assert client.get("/cron/close", headers={"X-Cron-Key": "wrong"}).status_code == 404
    assert client.get("/cron/close").status_code == 404


def test_healthz_shows_versions_to_site_admins_only(app, client):
    anonymous = app.test_client().get("/healthz").json
    assert anonymous["status"] == "ok" and set(anonymous["deps"].values()) == {"ok"}
    assert client.get("/healthz").json["deps"]["PIL"][0].isdigit()                # the test user is a site admin
