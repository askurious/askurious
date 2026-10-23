"""Which request counts as one of the developer's own computer (app.is_local_request): the only case in which the
development SECRET_KEY and DEV_LOGIN answer. Audit 2026-10-18, CFG-01 (and AUTH-4): with PROXY_COUNT=0 the Host
header alone decided, so "Host: localhost" from anywhere reached /auth/dev-login and the pages signed with the known
key; a server whose .env lacked PUBLIC_BASE_URL started with the local default."""
import pytest

from config import DEV_SECRET_KEY, Config

FAR = {"REMOTE_ADDR": "203.0.113.5"}


def _dev(app, **extra):
    """The test app as a developer's server: the development key, DEV_LOGIN, no proxy."""
    app.config.update(SECRET_KEY=DEV_SECRET_KEY, SECRET_KEY_FALLBACKS=[], DEV_LOGIN=True, PROXY_COUNT=0,
                      DEV_TRUST_BRIDGE=False, **extra)
    return app.test_client()


def _start(tmp_path, **extra):
    """create_app with these settings (the checks at the start refuse before any database is touched)."""
    from app import create_app

    settings = {"SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'start.db'}", "UPLOAD_FOLDER": str(tmp_path / "u"),
                "AI_PRICES_FILE": str(tmp_path / "ai_prices.json"), "PUBLIC_BASE_URL": "http://localhost:5060",
                "SECRET_KEY": DEV_SECRET_KEY, "DEV_LOGIN": False, "TESTING": True}
    server = extra.pop("server", False)
    settings.update(extra)
    return create_app(type("Start", (Config,), settings), server=server)


def test_host_localhost_from_another_computer_is_not_local(app):
    """CFG-01 a, c: without a proxy the address of the connection must be this computer as well; *.localhost too."""
    client = _dev(app)
    for host in ("localhost", "foo.localhost", "127.0.0.1"):
        assert client.get("/healthz", headers={"Host": host}, environ_base=FAR).status_code == 503, host
        assert client.get("/auth/dev-login", headers={"Host": host}, environ_base=FAR).status_code in (404, 503), host
    r = client.get("/auth/dev-login?orcid=0000-0000-0000-001X", headers={"Host": "localhost"}, environ_base=FAR)
    assert r.status_code in (404, 503) and not any("session" in h for h in r.headers.getlist("Set-Cookie"))
    # this computer itself, over IPv4, IPv6 and a dual-stack socket, as before
    for addr in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
        assert client.get("/healthz", headers={"Host": "localhost"},
                          environ_base={"REMOTE_ADDR": addr}).status_code == 200, addr
    assert client.get("/auth/dev-login", headers={"Host": "localhost"}).status_code == 302
    # with a real key DEV_LOGIN still answers this computer only
    app.config["SECRET_KEY"] = "k" * 64
    assert client.get("/auth/dev-login", headers={"Host": "localhost"}, environ_base=FAR).status_code == 404


def test_a_bridge_address_counts_only_with_dev_trust_bridge(app):
    """A development server in a container sees its host's bridge address: only with DEV_TRUST_BRIDGE=1, only
    without a proxy, and never a public address."""
    bridge = {"REMOTE_ADDR": "172.17.0.1"}
    client = _dev(app)
    assert client.get("/healthz", headers={"Host": "localhost"}, environ_base=bridge).status_code == 503
    app.config["DEV_TRUST_BRIDGE"] = True
    assert client.get("/healthz", headers={"Host": "localhost"}, environ_base=bridge).status_code == 200
    assert client.get("/healthz", headers={"Host": "localhost"}, environ_base=FAR).status_code == 503
    assert client.get("/healthz", headers={"Host": "survey.example"}, environ_base=bridge).status_code == 503
    assert client.get("/healthz", headers={"Host": "localhost"},
                      environ_base={"REMOTE_ADDR": "8.8.8.8"}).status_code == 503
    app.config["PROXY_COUNT"] = 1  # behind a proxy the bridge is the proxy's: never trusted
    assert client.get("/healthz", headers={"Host": "localhost"}, environ_base=bridge).status_code == 503


def test_dev_trust_bridge_never_starts_on_a_server(tmp_path):
    with pytest.raises(RuntimeError, match="DEV_TRUST_BRIDGE"):
        _start(tmp_path, DEV_TRUST_BRIDGE=True, PUBLIC_BASE_URL="https://survey.example",
               SECRET_KEY="Q7w-Rt5_zK9pL2mN8bV4cX6jH3gF1dS0aYx8Lp2Mn4Bv6Cx")


def test_a_server_without_public_base_url_does_not_start_with_the_development_key(tmp_path, monkeypatch):
    """CFG-01 b: passenger_wsgi.py (Plesk, gunicorn in Docker) is a server; without the line PUBLIC_BASE_URL in its
    .env the local default does not let the development key or DEV_LOGIN through. run.py and create_app() itself (the
    developer's computer) start as before."""
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="PUBLIC_BASE_URL is not set"):
        _start(tmp_path, server=True)
    with pytest.raises(RuntimeError, match="DEV_LOGIN"):
        _start(tmp_path, server=True, SECRET_KEY="k" * 64, DEV_LOGIN=True)
    # PUBLIC_BASE_URL named in the environment (a server set up on purpose under localhost): starts as before
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://localhost:5060")
    assert _start(tmp_path, server=True) is not None


def test_passenger_wsgi_starts_the_app_as_a_server():
    import os

    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "passenger_wsgi.py"), encoding="utf-8") as f:
        assert "create_app(server=True)" in f.read()
