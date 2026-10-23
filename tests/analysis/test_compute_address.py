"""The pseudonymised answers go to the compute node only over https, or over plain http inside the own machine or
network (security and code audit 2026-10-13, IN-6); otherwise this server computes itself."""
from app import compute


def test_plain_http_only_inside_the_own_network():
    for url in ("https://rechner.example.org", "http://compute:8090", "http://localhost:8090", "http://127.0.0.1:8090",
                "http://10.0.0.5:8090", "http://192.168.1.2"):
        assert compute.url_ok(url), url
    for url in ("http://rechner.example.org", "http://8.8.8.8:8090", "ftp://compute", "", "http://", "https://[::1"):
        assert not compute.url_ok(url), url


def test_not_enabled_with_plain_http_to_an_outside_host(app):
    app.config.update(COMPUTE_URL="http://rechner.example.org", COMPUTE_SECRET="s" * 32)
    assert not compute.enabled(app)
    app.config["COMPUTE_URL"] = "https://rechner.example.org"
    assert compute.enabled(app)
