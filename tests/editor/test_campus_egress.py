"""Fresh audit W3: publicly routed university addresses are still inside the server's trust zone. The operator names
its own networks (config.PROTECTED_NETS from instance_overlay/protected_nets.txt or the .env; the public code names
none, Ralf 2026-10-19); here an installation with a campus like the RWTH's."""
import ipaddress
import os
import socket
import subprocess
import sys

import pytest

import config
from app import ai_access, webservice

CAMPUS = ("134.61.0.0/16", "134.130.0.0/16", "137.226.0.0/16", "192.35.229.0/24", "194.35.108.0/22",
          "2a00:8a60::/29", "2001:67c:a20::/48")


@pytest.fixture(autouse=True)
def campus(monkeypatch):
    monkeypatch.setattr(config, "PROTECTED_NETS", CAMPUS)


@pytest.mark.parametrize("address", ["134.130.4.1", "137.226.3.4", "2a00:8a60:0:4::1",
                                      "134.61.3.4", "192.35.229.9", "194.35.111.1", "2001:67c:a20::1",
                                      "2a00:8a67:ffff::1", "::ffff:137.226.3.4",
                                      "2002:89e2:0304::1", "64:ff9b::89e2:304"])
def test_campus_addresses_and_embedded_forms_are_blocked_with_empty_config(app, monkeypatch, address):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    monkeypatch.setattr(webservice, "_resolve", lambda *args: [(family, socket.SOCK_STREAM, 6, "", (address, 443))])
    monkeypatch.setattr(webservice, "_own_ips", lambda *args: frozenset())
    calls = []
    monkeypatch.setattr(webservice, "_talk", lambda *args: calls.append(args) or (200, b"{}"))
    with app.app_context():
        app.config.update(WEBSERVICE_BLOCKED_NETS=[], AI_OWN_URL_HOSTS=[])
        assert not webservice.public(ipaddress.ip_address(address), nets=webservice.blocked_nets())
        with pytest.raises(webservice.Blocked):
            webservice.fetch("GET", "https://arbitrary.example/api")
        with pytest.raises(ai_access.AccessError):
            ai_access.check_url("https://arbitrary.example/v1")
    assert calls == []


def test_mixed_dns_answers_fail_and_public_apis_keep_working(app, monkeypatch):
    answers = ["93.184.216.34", "137.226.3.4"]
    monkeypatch.setattr(webservice, "_resolve", lambda *args: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443)) for address in answers])
    monkeypatch.setattr(webservice, "_own_ips", lambda *args: frozenset())
    calls = []
    monkeypatch.setattr(webservice, "_talk", lambda *args: calls.append(args) or (200, b"{}"))
    with app.app_context():
        with pytest.raises(webservice.Blocked):
            webservice.fetch("GET", "https://alias.example/api")
        assert not calls
        answers[:] = ["93.184.216.34"]
        assert webservice.fetch("GET", "https://alias.example/api") == (200, b"{}")
        assert ai_access.check_url("https://alias.example/v1") == "https://alias.example/v1"
        assert calls[0][2] == "93.184.216.34"  # still connects to the checked, pinned address


def test_the_operators_networks_come_from_its_overlay_and_env(tmp_path):
    """protected_nets.txt of the overlay and PROTECTED_NETS of the .env, read at the start; the public code has none."""
    (tmp_path / "protected_nets.txt").write_text("# campus\n137.226.0.0/16\n2001:67c:a20::/48  # hospital\n\n",
                                                 encoding="utf-8")
    code = "import config; print(config.PROTECTED_NETS); print(config.Config.WEBSERVICE_BLOCKED_NETS)"
    env = {k: v for k, v in os.environ.items() if k not in ("PROTECTED_NETS", "WEBSERVICE_BLOCKED_NETS")}
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    run = lambda **extra: subprocess.run([sys.executable, "-c", code], env=env | extra, cwd=root, capture_output=True,
                                         encoding="utf-8", check=True).stdout.split("\n")
    own, blocked = run(INSTANCE_OVERLAY=str(tmp_path), PROTECTED_NETS="134.61.0.0/16", WEBSERVICE_BLOCKED_NETS="93.184.216.0/24")[:2]
    assert own == str(("134.61.0.0/16", "137.226.0.0/16", "2001:67c:a20::/48"))
    assert blocked == str(["134.61.0.0/16", "137.226.0.0/16", "2001:67c:a20::/48", "93.184.216.0/24"])
    assert run(INSTANCE_OVERLAY=str(tmp_path / "none"))[0] == "()"


def test_installation_networks_extend_the_mandatory_campus_defaults(app):
    with app.app_context():
        app.config["WEBSERVICE_BLOCKED_NETS"] = ["93.184.216.0/24"]
        nets = webservice.blocked_nets()
        assert not webservice.public(ipaddress.ip_address("93.184.216.34"), nets=nets)
        assert not webservice.public(ipaddress.ip_address("137.226.3.4"), nets=nets)
        assert not webservice.public(ipaddress.ip_address("2a00:8a67::1"), nets=nets)


def test_an_explicit_ai_host_exception_does_not_allow_other_campus_hosts(app, monkeypatch):
    monkeypatch.setattr(webservice, "_resolve", lambda *args: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("137.226.3.4", 443))])
    monkeypatch.setattr(webservice, "_own_ips", lambda *args: frozenset())
    with app.app_context():
        app.config["AI_OWN_URL_HOSTS"] = ["approved.example"]
        assert ai_access.check_url("https://approved.example/v1") == "https://approved.example/v1"
        for host in ("other.example", "evil.approved.example"):
            with pytest.raises(ai_access.AccessError):
                ai_access.check_url(f"https://{host}/v1")


def test_fixed_ai_providers_on_campus_addresses_are_called_typed_hosts_are_not(app, monkeypatch):
    """chat.kiconnect.nrw lies in 134.130.0.0/16: the transport of an own access let only AI_OWN_URL_HOSTS pass, so
    every KI:connect call ended as "nicht erreichbar" (2026-10-15). Fixed providers pass; a typed host on the same
    campus address stays blocked and nothing is sent."""
    import httpx

    monkeypatch.setattr(webservice, "_resolve", lambda *args: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("134.130.66.31", 443))])
    monkeypatch.setattr(webservice, "_own_ips", lambda *args: frozenset())
    seen = []

    def answer(request):
        seen.append(request.url.host)
        return httpx.Response(200, json={"object": "list", "data": [
            {"id": "gpt-6-luna", "object": "model", "created": 0, "owned_by": "kiconnect"}]})

    monkeypatch.setattr(httpx, "HTTPTransport", lambda **kwargs: httpx.MockTransport(answer))
    with app.app_context():
        app.config.update(WEBSERVICE_BLOCKED_NETS=[], AI_OWN_URL_HOSTS=[])
        c = ai_access.client(None, base_url=ai_access.PROVIDERS["kiconnect"], key="k")
        assert ai_access.model_ids(c) == ["gpt-6-luna"]
        assert seen == ["chat.kiconnect.nrw"]
        c = ai_access.client(None, base_url="https://llm.example.org/v1", key="k")
        assert ai_access.model_ids(c) == []
    assert seen == ["chat.kiconnect.nrw"]
