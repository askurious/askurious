from tests.conftest import allow


def test_old_host_redirects_permanently(app):
    app.config.update(REDIRECT_HOSTS=["queryous.philipp-brauner.de"], PUBLIC_BASE_URL="https://queryous.de",
                      SECRET_KEY="x" * 32)  # a server with addresses of its own has a key of its own
    c = app.test_client()
    r = c.get("/s/abc123?gruppe=kurs-a", headers={"Host": "queryous.philipp-brauner.de"})
    assert r.status_code == 301 and r.headers["Location"] == "https://queryous.de/s/abc123?gruppe=kurs-a"
    r = c.post("/s/abc123", headers={"Host": "Queryous.Philipp-Brauner.de:443"})
    assert r.status_code == 308 and r.headers["Location"] == "https://queryous.de/s/abc123"
    assert c.get("/", headers={"Host": "queryous.de"}).status_code == 200      # the new address serves normally


def test_users_get_registration_numbers(app):
    from app.extensions import db
    from app.models import User

    allow(app, "*")
    for orcid in ("0000-0000-0000-001X", "0000-0002-0000-0001", "0000-0000-0000-001X", "0000-0002-0000-0002"):
        app.test_client().get(f"/auth/dev-login?orcid={orcid}&name=X")
    with app.app_context():
        assert [u.number for u in db.session.scalars(db.select(User).order_by(User.id))] == [1, 2, 3]
    c = app.test_client()
    c.get("/auth/dev-login?orcid=0000-0000-0000-001X&name=X")
    c.post("/terms/", data={"accept": "1"})
    assert "(Kontonummer 1)" in c.get("/account/", follow_redirects=True).data.decode()  # behind the ORCID iD


def test_imprint_page_and_footer(app, client):
    r = client.get("/privacy")
    assert r.status_code == 301 and r.headers["Location"].endswith("/imprint")
    app.config["IMPRINT_ADDRESS"] = []
    html = client.get("/imprint").data.decode()
    assert "Impressum und Datenschutz" in html and "§ 5 DDG" not in html          # no address configured
    app.config.update(IMPRINT_ADDRESS=["Musterstraße 1", "52062 Aachen"], IMPRINT_EMAIL="kontakt@example.org")
    html = client.get("/imprint").data.decode()
    assert "§ 5 DDG" in html and "52062 Aachen" in html and "kontakt@example.org" in html
    home = client.get("/").data.decode()
    assert 'class="footer-brand" href="/"><svg class="wordmark " viewBox="0 0 6249 1325" role="img" aria-label="Askurious.de">' in home and "Impressum &amp; Datenschutz" in home
    # the authors' affiliation behind the names (Philipp, 2026-10-05); still no operator line
    footer = home.split('class="site-footer"')[1]
    assert "Communication Science, RWTH Aachen University, Germany" in footer and "Betrieben von" not in home


def test_refuses_to_start_behind_https_with_the_development_secret_key(app):
    import pytest

    from app import create_app
    from config import DEV_SECRET_KEY

    settings = {k: v for k, v in app.config.items() if k.isupper()}
    settings["DEV_LOGIN"] = False   # refused behind https as well (tests/platform/test_hardening.py)
    for key in (DEV_SECRET_KEY, ""):
        cfg = type("C", (), {**settings, "PUBLIC_BASE_URL": "https://queryous.de", "SECRET_KEY": key})
        with pytest.raises(RuntimeError, match="SECRET_KEY"):
            create_app(cfg)
    create_app(type("C", (), {**settings, "PUBLIC_BASE_URL": "https://queryous.de",  # a random key (CFG-02)
                              "SECRET_KEY": __import__("secrets").token_hex(32)}))
    create_app(type("C", (), {**settings, "SECRET_KEY": DEV_SECRET_KEY}))   # local http development still works
