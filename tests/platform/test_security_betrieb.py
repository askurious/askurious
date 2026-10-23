"""Security audit 2026-10-03, package 4 (Betrieb und Kopfzeilen): S4, H12, H2."""
import os

ME = "0000-0000-0000-001X"
DEV_LOGIN = f"/auth/dev-login?orcid={ME}&name=X"


def _proxied(app, **changes):
    """A second app on the test database, with other settings: ProxyFix is put in front by create_app."""
    from app import create_app

    settings = {k: v for k, v in app.config.items() if k.isupper()}
    return create_app(type("C", (), {**settings, "PROXY_COUNT": 1, **changes}))


# --- S4 case 2: X-Forwarded-Host no longer makes a request local -------------------------------------------------

def test_forwarded_host_does_not_pass_the_development_key_guard(app):
    """The reproduction of the report: development key, DEV_LOGIN, local PUBLIC_BASE_URL, PROXY_COUNT=1. A request
    under a foreign name got 503, the same one with "X-Forwarded-Host: localhost" was signed in as admin."""
    from config import DEV_SECRET_KEY

    c = _proxied(app, SECRET_KEY=DEV_SECRET_KEY, DEV_LOGIN=True).test_client()
    assert c.get(DEV_LOGIN, headers={"Host": "queryous.example"}).status_code == 503
    r = c.get(DEV_LOGIN, headers={"Host": "queryous.example", "X-Forwarded-Host": "localhost"})
    assert r.status_code == 503
    assert c.get("/dashboard/", headers={"Host": "localhost"}).status_code == 302          # not signed in


def test_forwarded_host_does_not_open_dev_login(app):
    c = _proxied(app, SECRET_KEY="x" * 32, DEV_LOGIN=True).test_client()
    for headers in ({"Host": "queryous.example", "X-Forwarded-Host": "localhost"},
                    {"Host": "queryous.example", "X-Forwarded-Host": "localhost", "X-Forwarded-For": "127.0.0.1"}):
        assert c.get(DEV_LOGIN, headers=headers).status_code == 404, headers
    assert c.get("/dashboard/").status_code == 302


def test_behind_a_proxy_the_client_must_be_this_computer_too(app):
    """A proxy that passes the Host header on as it came (an nginx default server) would let "Host: localhost"
    through from anywhere; behind a proxy the address it appended must be a loopback address as well."""
    from config import DEV_SECRET_KEY

    dev = _proxied(app, SECRET_KEY=DEV_SECRET_KEY, DEV_LOGIN=True)
    assert dev.test_client().get(DEV_LOGIN, headers={"Host": "localhost", "X-Forwarded-For": "203.0.113.7"}) \
        .status_code == 503
    keyed = _proxied(app, SECRET_KEY="x" * 32, DEV_LOGIN=True)
    assert keyed.test_client().get(DEV_LOGIN, headers={"Host": "localhost", "X-Forwarded-For": "203.0.113.7"}) \
        .status_code == 404
    # a client that put "127.0.0.1" in front itself: the proxy appended its real address, and that one counts
    assert keyed.test_client().get(DEV_LOGIN, headers={"Host": "localhost",
                                                       "X-Forwarded-For": "127.0.0.1, 203.0.113.7"}).status_code == 404
    # the developer's own browser behind a local proxy still works, under every local name
    for host, client_addr in (("localhost:5092", "127.0.0.1"), ("queryous.test", "::1")):
        for one in (dev, keyed):
            r = one.test_client().get(DEV_LOGIN, headers={"Host": host, "X-Forwarded-For": client_addr})
            assert r.status_code == 302, (host, client_addr)


def test_without_proxy_count_only_the_host_counts(app):
    """No proxy: X-Forwarded-* are not read at all. Since the audit of 2026-10-18 (CFG-01) the connection's own
    address must be this computer as well; a development server in a container (bridge address) needs
    DEV_TRUST_BRIDGE=1 (tests/platform/test_local_request.py)."""
    app.config["SECRET_KEY"] = "x" * 32
    c = app.test_client()
    assert c.get(DEV_LOGIN, headers={"Host": "queryous.example", "X-Forwarded-Host": "localhost"}).status_code == 404
    bridge = {"REMOTE_ADDR": "172.17.0.1"}
    assert c.get(DEV_LOGIN, headers={"Host": "localhost"}, environ_overrides=bridge).status_code == 404
    app.config["DEV_TRUST_BRIDGE"] = True
    assert c.get(DEV_LOGIN, headers={"Host": "localhost"}, environ_overrides=bridge).status_code == 302


# --- H12: the redirect of old host names reads the Host header as nginx passes it --------------------------------

def test_old_host_redirect_reads_the_host_header_behind_a_proxy(app):
    c = _proxied(app, SECRET_KEY=__import__("secrets").token_hex(32), DEV_LOGIN=False,  # a random key (CFG-02)
                 REDIRECT_HOSTS=["queryous.philipp-brauner.de"],
                 PUBLIC_BASE_URL="https://queryous.de").test_client()
    r = c.get("/s/abc?x=1", headers={"Host": "queryous.philipp-brauner.de", "X-Forwarded-For": "198.51.100.4",
                                     "X-Forwarded-Proto": "https"})
    assert r.status_code == 301 and r.headers["Location"] == "https://queryous.de/s/abc?x=1"
    # a forwarded name is no longer taken, in either direction
    r = c.get("/", headers={"Host": "queryous.de", "X-Forwarded-Host": "queryous.philipp-brauner.de"})
    assert r.status_code == 200
    r = c.get("/s/abc", headers={"Host": "queryous.philipp-brauner.de", "X-Forwarded-Host": "queryous.de"})
    assert r.status_code == 301


def test_forwarded_address_and_scheme_are_still_taken(app):
    """x_for and x_proto stay: the IP region and the limits need the participant's address."""
    keyed = _proxied(app, SECRET_KEY="x" * 32)
    seen = {}

    @keyed.route("/_seen")
    def _seen():
        from flask import request
        seen.update(addr=request.remote_addr, scheme=request.scheme, host=request.host)
        return ""

    keyed.test_client().get("/_seen", headers={"Host": "queryous.de", "X-Forwarded-For": "8.8.8.8, 198.51.100.4",
                                               "X-Forwarded-Proto": "https", "X-Forwarded-Host": "evil.example"})
    assert seen == {"addr": "198.51.100.4", "scheme": "https", "host": "queryous.de"}


# --- S4: the admin page explains how to verify PROXY_COUNT --------------------------------------------------------

def test_ip_region_page_explains_the_proxy_check(app, client):
    page = client.get("/admin/database").data.decode()
    assert "Proxy-Einstellung" in page and "nicht gesetzt" in page and "mit 0 Einträgen" in page
    assert "Zahl der Proxys vor der App" in page and "X-Forwarded-For: 8.8.8.8" in page
    page = client.get("/admin/database", headers={"X-Forwarded-For": "8.8.8.8, 198.51.100.4"}).data.decode()
    assert "mit 2 Einträgen" in page and "8.8.8.8</" not in page               # the count only, no address shown


# --- H2: what a new SECRET_KEY breaks is written down --------------------------------------------------------------

def test_secret_key_consequences_are_documented(app):
    root = os.path.dirname(app.root_path)
    with open(os.path.join(root, "README.md"), encoding="utf-8") as f:
        readme = f.read()
    with open(os.path.join(root, "config.py"), encoding="utf-8") as f:
        config = f.read()
    for text in (readme, config):
        assert "deletion codes" in text and "return links" in text
    assert "number of proxies in front of the app" in readme.lower()
