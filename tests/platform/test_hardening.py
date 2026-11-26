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


def test_cron_takes_post_with_the_key_in_the_header_only(app, client):
    """The key is never read from the address (it would end up in access logs), the route changes state and so takes
    POST only, and wrong keys are counted per sender like other secrets."""
    from app import limits

    limits._fallback.clear()
    assert client.post("/cron/close", headers={"X-Cron-Key": "k"}).status_code == 200
    assert client.post("/cron/close?key=k").status_code == 404
    assert client.get("/cron/close", headers={"X-Cron-Key": "k"}).status_code == 405
    assert client.post("/cron/close", headers={"X-Cron-Key": "wrong"}).status_code == 404
    assert client.post("/cron/close").status_code == 404
    app.config["TOKEN_TRY_LIMIT"] = 3
    env = {"REMOTE_ADDR": "203.0.113.77"}
    for _ in range(3):
        assert client.post("/cron/close", headers={"X-Cron-Key": "wrong"}, environ_base=env).status_code == 404
    assert client.post("/cron/close", headers={"X-Cron-Key": "k"}, environ_base=env).status_code == 404   # locked out
    assert client.post("/cron/close", headers={"X-Cron-Key": "k"}).status_code == 200                      # others not


def test_healthz_shows_versions_to_site_admins_only(app, client):
    anonymous = app.test_client().get("/healthz").json
    assert anonymous["status"] == "ok" and set(anonymous["deps"].values()) == {"ok"}
    assert client.get("/healthz").json["deps"]["PIL"][0].isdigit()                # the test user is a site admin


def test_going_back_after_a_form_never_leaves_the_site(app, client, survey):
    """The page a form came from (the Referer) is taken as the way back only when it is a page of this host."""
    from app.links import same_site_path

    with app.test_request_context("/", base_url="https://askurious.example"):
        assert same_site_path("https://askurious.example/surveys/1/editor?lang=en") == "/surveys/1/editor?lang=en"
        for other in ("https://evil.example/surveys/1/editor", "//evil.example/x", "javascript:alert(1)", "", None,
                      "https://askurious.example.evil.example/", "https://askurious.example\\@evil.example/"):
            assert same_site_path(other) == "", other
    r = client.post("/surveys/1/editor/undo", headers={"Referer": "https://evil.example/there"})
    assert r.status_code == 302 and "evil.example" not in r.headers["Location"]
    r = client.post("/surveys/1/editor/undo", headers={"Referer": "http://localhost/surveys/1/editor?x=1"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/surveys/1/editor?x=1")


def test_only_the_named_add_on_modules_load(app, monkeypatch, caplog):
    import logging
    from types import SimpleNamespace

    from app import plugins

    loaded = []

    def entry(name):
        return SimpleNamespace(name=name, load=lambda: SimpleNamespace(init_app=lambda a: loaded.append(name)))

    monkeypatch.setattr(plugins, "entry_points", lambda group: [entry("eins"), entry("zwei")])
    monkeypatch.setattr(plugins, "LOADED", [])
    app.config["ASKURIOUS_PLUGINS"] = ["zwei"]
    with caplog.at_level(logging.WARNING, logger=app.logger.name):
        plugins.load(app)
    assert loaded == ["zwei"] and plugins.LOADED == ["zwei"] and "eins" in caplog.text
    monkeypatch.setattr(plugins, "LOADED", [])
    app.config["ASKURIOUS_PLUGINS"] = None                                   # unset: every installed one
    plugins.load(app)
    assert loaded == ["zwei", "eins", "zwei"]


def test_config_reads_the_list_of_add_on_modules(monkeypatch):
    import importlib

    import config

    for value, expected in ((None, None), ("", None), ("a, b", ["a", "b"]), ("-", [])):
        if value is None:
            monkeypatch.delenv("ASKURIOUS_PLUGINS", raising=False)
        else:
            monkeypatch.setenv("ASKURIOUS_PLUGINS", value)
        assert importlib.reload(config).Config.ASKURIOUS_PLUGINS == expected, value
    monkeypatch.delenv("ASKURIOUS_PLUGINS", raising=False)
    importlib.reload(config)


def test_links_of_the_handbook_lead_nowhere_else(app):
    from app import handbook_md

    with app.test_request_context("/"):
        assert handbook_md._href("https://example.org/x") == ("https://example.org/x", True)
        assert handbook_md._href("mailto:a@example.org")[1] is True
        for bad in ("javascript:alert(1)", "data:text/html,x", "//evil.example/x", "vbscript:x"):
            assert handbook_md._href(bad) == ("#", False), bad
        assert handbook_md._href("bild.png") == ("bild.png", False)


def test_account_numbers_are_masked_for_an_ai_service():
    from app import ai_privacy

    assert ai_privacy.redact("Bitte auf DE89 3704 0044 0532 0130 00 überweisen") == "Bitte auf [IBAN] überweisen"
    assert ai_privacy.redact("IBAN GB29NWBK60161331926819.") == "IBAN [IBAN]."
    assert ai_privacy.redact("Das Modell AB12 hat vier Räder") == "Das Modell AB12 hat vier Räder"
