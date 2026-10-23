"""Web service in the flow, guarded (code audit 2026-10-08, W1-W5, W7-W12): a time limit for the whole call, no
transaction open during it, the experimental switch also for files and copies, port 443 only and not this server's
own addresses, nothing before the consent that passes on answers, the test call small and plain, the key not to
another server, header values checked, the weather demo sparing, NAT64 and site-local IPv6 blocked."""
import datetime
import io
import ipaddress
import json
import re
import socket
import ssl
import threading
import time

import pytest
from sqlalchemy import text

from app import demo_webapp, flow, webservice
from app.extensions import db
from app.models import QuotaCell, Response, Survey
from tests.conftest import CONSENT_OK, experiments_on
from tests.editor.test_webservice import E, build, node_form, start

ANSWER = b'{"current": {"temperature_2m": 13.8}, "current_units": {"temperature_2m": "C"}}'


@pytest.fixture(autouse=True)
def fresh_own():
    webservice._own.update(key=None, at=0.0, ips=frozenset())
    yield
    webservice._own.update(key=None, at=0.0, ips=frozenset())


def resolver(table):
    """socket.getaddrinfo answering per host name (localhost and the like stay local)."""
    def getaddrinfo(host, port, *a, **k):
        address = table.get(host, "127.0.0.1")
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (address, port))]
    return getaddrinfo


# --- W1: one time limit for the whole call ------------------------------------------------------------------------


def _tls_server(tmp_path, serve):
    """A local TLS server for trickle.example; returns (port, certificate file)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "trickle.example")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(1)
            .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=2))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("trickle.example")]), False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), True).sign(key, hashes.SHA256()))
    cf, kf = tmp_path / "c.pem", tmp_path / "k.pem"
    cf.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    kf.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption()))
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cf, kf)
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def run():
        conn, _ = srv.accept()
        try:
            serve(ctx.wrap_socket(conn, server_side=True))
        except OSError:
            pass
        finally:
            conn.close()
            srv.close()

    threading.Thread(target=run, daemon=True).start()
    return srv.getsockname()[1], str(cf)


def _to_local(monkeypatch, port, cert):
    import certifi

    monkeypatch.setattr(certifi, "where", lambda: cert)
    monkeypatch.setattr(webservice, "_public_address", lambda *a: "127.0.0.1")
    monkeypatch.setattr(webservice, "HTTPS_PORT", port)
    monkeypatch.setattr(webservice, "TIMEOUT", 1.5)


def test_a_server_that_trickles_is_cut_off_at_the_time_limit(tmp_path, monkeypatch):
    def trickle(s):
        s.recv(4096)
        s.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 1000\r\n\r\n")
        for _ in range(12):
            time.sleep(0.5)
            s.sendall(b" ")

    port, cert = _tls_server(tmp_path, trickle)
    _to_local(monkeypatch, port, cert)
    started = time.monotonic()
    with pytest.raises(OSError):
        webservice.fetch("GET", "https://trickle.example/x")
    assert time.monotonic() - started < 3.5


def test_a_server_that_answers_in_time_is_read(tmp_path, monkeypatch):
    def answer(s):
        s.recv(4096)
        s.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n" % len(ANSWER) + ANSWER)

    port, cert = _tls_server(tmp_path, answer)
    _to_local(monkeypatch, port, cert)
    assert webservice.fetch("GET", "https://trickle.example/x") == (200, ANSWER)


def test_the_name_lookup_has_a_time_limit_too(monkeypatch):
    def slow(*a, **k):
        time.sleep(2)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", slow)
    monkeypatch.setattr(webservice, "TIMEOUT", 0.3)
    started = time.monotonic()
    with pytest.raises(webservice.Unreachable):
        webservice.fetch("GET", "https://slow-dns.example/")
    assert time.monotonic() - started < 1.5


def test_too_many_calls_at_once_get_no_thread(monkeypatch):
    taken = 0
    while webservice._slots.acquire(blocking=False):
        taken += 1
    try:
        result = webservice.call({"url": "https://api.example/", "values": [{"name": "x", "path": "x", "kind": "text"}]}, {})
        assert result["ok"] == 0 and result["err"].startswith("busy")
    finally:
        for _ in range(taken):
            webservice._slots.release()


# --- W2: no transaction open while the server waits ---------------------------------------------------------------


def test_the_call_waits_until_the_step_is_stored_and_no_cell_is_locked(app, client, survey, monkeypatch):
    from tests.quotas.test_quotas import build as quota_build
    from tests.quotas.test_quotas import screen
    from tests.quotas.test_quotas import start as quota_start

    seen = {}

    def fetch(method, url, body=None, headers=None):
        seen["in_transaction"] = db.session().in_transaction()
        if db.engine.dialect.name == "postgresql":
            with db.engine.connect() as other:
                try:
                    other.execute(text(f"SELECT id FROM {QuotaCell.__tablename__} FOR UPDATE NOWAIT"))
                    seen["locked"] = False
                except Exception as e:  # LockNotAvailable
                    seen["locked"] = type(getattr(e, "orig", e)).__name__
                other.rollback()
        return 200, b'{"a": "x"}'

    monkeypatch.setattr(webservice, "fetch", fetch)
    quota_build(app, client, cells=[("Männer", "m", 5, [(2, "eq", "1")]), ("Frauen", "f", 5, [(2, "eq", "2")])])
    with app.app_context():
        s = db.session.get(Survey, 1)
        import copy

        root = copy.deepcopy(flow.tree(s))
        i = next(n for n, c in enumerate(root["children"]) if c.get("t") == "quota")
        root["children"].insert(i + 1, {"t": "ws", "id": "w1", "code": "ws1", "name": "W", "method": "GET",
                                        "url": "https://x.example/a", "body": "",
                                        "values": [{"name": "a", "path": "a", "kind": "text"}]})
        s.flow = root
        flow.normalize(s)
        db.session.commit()
    uid = quota_start(app, client)
    screen(app, uid)
    assert seen.get("in_transaction") is False
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(source="real"))
        assert r.external[webservice.KEY]["w1"]["v"] == {"a": "x"}
        assert db.session.scalar(db.select(QuotaCell).filter_by(code="m")).running == 1
        if db.engine.dialect.name == "postgresql":
            assert seen.get("locked") is False


# --- W3: the experimental switch also for files and copies --------------------------------------------------------


def _import(client, body):
    page = client.post("/surveys/new/import", data={"file": (io.BytesIO(body), "survey.json")},
                       content_type="multipart/form-data", follow_redirects=True).data.decode()
    token = re.search(r'name="token" value="([^"]+)"', page).group(1)
    return client.post("/surveys/new/import", data={"token": token, "title-0": "Kopie"}, follow_redirects=True).data.decode()


def test_a_file_or_a_copy_brings_a_web_service_only_to_whom_it_is_switched_on(app, client, survey, monkeypatch):
    calls = []
    monkeypatch.setattr(webservice, "fetch", lambda *a, **k: calls.append(a) or (200, ANSWER))
    nid = build(app, client)
    body = client.get("/surveys/1/export/survey.json").get_data()
    experiments_on(client)  # all off
    page = _import(client, body)
    assert "Web Service" in page and "nicht freigeschaltet" in page
    with app.app_context():
        copy_id = db.session.scalars(db.select(Survey).order_by(Survey.id.desc())).first().id
        assert flow.walk_nodes(db.session.get(Survey, copy_id), "ws") == []
    page = client.post("/surveys/1/duplicate", data={"title": "Welle 2", "with_people": "1"}, follow_redirects=True).data.decode()
    assert "nicht freigeschaltet" in page
    with app.app_context():
        twin = db.session.scalars(db.select(Survey).order_by(Survey.id.desc())).first()
        assert twin.id != copy_id and flow.walk_nodes(twin, "ws") == []
    # in the survey that has it, nobody without the switch changes the address or makes the test call
    page = client.post(f"{E}/flow/{nid}/update", data=node_form(url="https://evil.example/x?c={{city}}"),
                       follow_redirects=True).data.decode()
    assert "nicht freigeschaltet" in page or "freigeschaltet ist" in page
    with app.app_context():
        assert flow.walk_nodes(db.session.get(Survey, 1), "ws")[0]["url"].startswith("https://api.open-meteo.com")
    assert client.post(f"{E}/flow/{nid}/ws-test", data={"test_city": "x"}).status_code == 403
    assert calls == []
    # the name and the values still change (they call nobody)
    page = client.post(f"{E}/flow/{nid}/update", data=node_form(values=(("temp", "current.temperature_2m", "number"),)),
                       follow_redirects=True).data.decode()
    with app.app_context():
        node = flow.walk_nodes(db.session.get(Survey, 1), "ws")[0]
        assert [v["name"] for v in node["values"]] == ["temp"] and node["url"].startswith("https://api.open-meteo.com")


def test_with_the_switch_the_file_keeps_its_web_service(app, client, survey):
    build(app, client)
    body = client.get("/surveys/1/export/survey.json").get_data()
    _import(client, body)
    with app.app_context():
        copy = db.session.scalars(db.select(Survey).order_by(Survey.id.desc())).first()
        assert flow.walk_nodes(copy, "ws")[0]["url"].startswith("https://api.open-meteo.com")


# --- W4: port 443, not this server, no telling answers ------------------------------------------------------------


def test_only_port_443(app, client, survey):
    assert webservice.url_error("https://api.example:8443/x")
    assert webservice.url_error("https://api.example:443/x") is None
    with pytest.raises(webservice.Blocked):
        webservice.fetch("GET", "https://api.example:22/")
    nid = build(app, client)
    page = client.post(f"{E}/flow/{nid}/update", data=node_form(url="https://api.example:8080/x"), follow_redirects=True)
    assert "443" in page.data.decode()


def test_this_servers_own_addresses_are_never_called(app, monkeypatch):
    import config

    monkeypatch.setattr(config, "PROTECTED_NETS", ("137.226.0.0/16",))  # an operator's campus (protected_nets.txt)
    monkeypatch.setattr(socket, "getaddrinfo", resolver({"ask.example.org": "198.51.100.7", "alias.example": "198.51.100.7",
                                                         "api.example": "93.184.216.34", "inst.example": "137.226.3.4"}))
    monkeypatch.setattr(webservice, "_talk", lambda *a: (200, b"{}"))
    with app.test_request_context():
        app.config.update(PARTICIPANT_BASE_URL="https://ask.example.org")
        with pytest.raises(webservice.Blocked):
            webservice.fetch("GET", "https://ask.example.org/s/x")      # by name
        with pytest.raises(webservice.Blocked):
            webservice.fetch("GET", "https://alias.example/")            # by its address
        assert webservice.fetch("GET", "https://api.example/") == (200, b"{}")
        with pytest.raises(webservice.Blocked):
            webservice.fetch("GET", "https://inst.example/")  # the campus is protected even with no extra configuration


@pytest.mark.parametrize("answer", [OSError("not reachable"), webservice.Unreachable("not reachable"),
                                    webservice.Unreachable("unknown host x")])
def test_the_test_call_says_only_not_reachable(monkeypatch, answer):
    def fetch(*a, **k):
        raise answer

    monkeypatch.setattr(webservice, "fetch", fetch)
    out = webservice.test({"url": "https://api.example/", "values": []}, {})
    assert out["err"] in ("nicht erreichbar", "not reachable") and not out["snippet"]


def test_no_connection_and_no_tls_look_the_same(tmp_path, monkeypatch):
    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    port = closed.getsockname()[1]
    closed.close()                                                   # nobody listens here
    monkeypatch.setattr(webservice, "_public_address", lambda *a: "127.0.0.1")
    monkeypatch.setattr(webservice, "HTTPS_PORT", port)
    first = webservice.test({"url": "https://api.example/", "values": []}, {})
    plain = socket.socket()
    plain.bind(("127.0.0.1", 0))
    plain.listen(1)

    def talk():
        conn, _ = plain.accept()
        conn.recv(4096)
        conn.sendall(b"SSH-2.0-OpenSSH_9.6\r\n")
        conn.close()

    threading.Thread(target=talk, daemon=True).start()
    monkeypatch.setattr(webservice, "HTTPS_PORT", plain.getsockname()[1])
    second = webservice.test({"url": "https://api.example/", "values": []}, {})
    plain.close()
    assert first["err"] == second["err"] and first["err"] in ("nicht erreichbar", "not reachable")


def test_no_part_of_an_error_page_is_shown(monkeypatch):
    monkeypatch.setattr(webservice, "fetch", lambda *a, **k: (404, b"<html>Intranet: printer admin</html>"))
    out = webservice.test({"url": "https://api.example/", "values": []}, {})
    assert out["status"] == 404 and out["size"] == len(b"<html>Intranet: printer admin</html>") and out["snippet"] == "" and "printer" not in json.dumps(out)
    monkeypatch.setattr(webservice, "fetch", lambda *a, **k: (200, b"<html>printer admin</html>"))
    out = webservice.test({"url": "https://api.example/", "values": []}, {})
    assert out["snippet"] == "" and "printer" not in json.dumps(out)


# --- W5: nothing before the consent that passes on answers --------------------------------------------------------


def test_a_web_service_before_the_consent_waits_for_it(app, client, survey, monkeypatch):
    calls = []
    monkeypatch.setattr(webservice, "fetch", lambda *a, **k: calls.append(a) or (200, ANSWER))
    nid = build(app, client)
    client.post(f"{E}/flow/{nid}/place", data={"target": "k1", "mode": "before"}, follow_redirects=True)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert flow.tree(s)["children"][0]["t"] == "ws"
        assert any("vor der Einwilligung" in m for _, m, _ in flow.check(s))
    uid = start(app, client)
    for _ in range(3):
        app.test_client().get(f"/s/{uid}")                          # opening the link calls nobody
    assert calls == []
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)                             # consented: now it is called, once
    assert len(calls) == 1
    p.post(f"/s/{uid}", data={"nav": "next", "i2": "Aachen"})
    assert len(calls) == 1


def test_a_web_service_that_passes_nothing_on_runs_before_the_consent_once_stored(app, client, survey, monkeypatch):
    calls = []
    monkeypatch.setattr(webservice, "fetch", lambda *a, **k: calls.append(a) or (200, ANSWER))
    nid = build(app, client)
    client.post(f"{E}/flow/{nid}/update", data=node_form(url="https://api.example/x?lang={{lang}}"))
    client.post(f"{E}/flow/{nid}/place", data={"target": "k1", "mode": "before"}, follow_redirects=True)
    with app.app_context():
        assert not any("vor der Einwilligung" in m for _, m, _ in flow.check(db.session.get(Survey, 1)))
    uid = start(app, client)
    for _ in range(3):
        app.test_client().get(f"/s/{uid}")                          # not stored yet: crawlers, link previews
    assert calls == []
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    assert len(calls) == 1


# --- W7: the instructions of the weather demo ---------------------------------------------------------------------


def test_the_instructions_name_the_real_addresses(client):
    import pathlib

    doc = (pathlib.Path(__file__).parents[2] / "docs" / "externer-abschnitt-signatur.md").read_text(encoding="utf-8")
    assert "/demo/weather?lang={{lang}}&return={{return}}" in doc and "/s/<Code>/back?t=" in doc
    assert "/demo/wetter" not in doc and "/zurueck" not in doc
    assert client.get("/demo/weather").status_code == 200


# --- W8: the test call fits the session cookie --------------------------------------------------------------------


def test_a_large_test_answer_fits_the_session_cookie(app, client, survey, monkeypatch):
    values = {f"v{i}": "x" * 500 for i in range(20)}
    monkeypatch.setattr(webservice, "fetch", lambda *a, **k: (200, json.dumps(values).encode()))
    nid = build(app, client)
    client.post(f"{E}/flow/{nid}/update", data=node_form(values=[(k, k, "text") for k in values]))
    r = client.post(f"{E}/flow/{nid}/ws-test", data={"test_city": "Aachen" * 30})
    cookies = [h for h in r.headers.getlist("Set-Cookie") if h.startswith("session=")]
    assert cookies and max(len(c) for c in cookies) < 4093
    page = client.get(r.headers["Location"]).data.decode()
    assert "Testaufruf:" in page and "HTTP 200" in page and "Gekürzt" in page


# --- W9, W10: the key, and what a header can carry ----------------------------------------------------------------


def test_the_key_does_not_go_to_another_server(app, client, survey):
    nid = build(app, client)
    client.post(f"{E}/flow/{nid}/update", data=node_form(header="X-Api-Key", secret="geheim123"))

    def node():
        with app.app_context():
            return flow.walk_nodes(db.session.get(Survey, 1), "ws")[0]

    same_server = "https://api.open-meteo.com/v1/other?city={{city}}"
    client.post(f"{E}/flow/{nid}/update", data=node_form(url=same_server, header="X-Api-Key"))
    assert node().get("secret") and node()["url"] == same_server     # the same server: kept
    page = client.post(f"{E}/flow/{nid}/update", data=node_form(url="https://collector.example/x?c={{city}}",
                                                               header="X-Api-Key"), follow_redirects=True).data.decode()
    assert node()["url"].startswith("https://collector.example") and not node().get("secret")
    assert "anderen Server" in page
    client.post(f"{E}/flow/{nid}/update", data=node_form(url="https://collector.example/y", header="X-Api-Key",
                                                         secret="neu456"))
    assert node().get("secret")                                      # entered for the new server: kept


@pytest.mark.parametrize("secret", ["schlüssel", "a€b", "a\r\nX-Evil: 1"])
def test_a_header_value_a_header_cannot_carry_is_refused(app, client, survey, secret):
    nid = build(app, client)
    page = client.post(f"{E}/flow/{nid}/update", data=node_form(header="X-Api-Key", secret=secret),
                       follow_redirects=True).data.decode()
    assert "ASCII" in page
    with app.app_context():
        assert not flow.walk_nodes(db.session.get(Survey, 1), "ws")[0].get("secret")


def test_a_header_refused_by_http_client_is_no_500(app, client, survey, monkeypatch):
    def fetch(*a, **k):
        raise ValueError("Invalid header value")

    monkeypatch.setattr(webservice, "fetch", fetch)
    nid = build(app, client)
    r = client.post(f"{E}/flow/{nid}/ws-test", data={"test_city": "x"}, follow_redirects=True)
    assert r.status_code == 200 and "Kopfzeile enthält Zeichen" in r.data.decode()


# --- W11: the weather demo asks sparingly -------------------------------------------------------------------------


def test_the_weather_demo_limits_each_sender_and_keeps_a_small_cache(client, monkeypatch):
    calls = []

    class Answer:
        def __init__(self, data):
            self._data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self._data

    def get(url, params=None, timeout=None):
        calls.append(url)
        if url == demo_webapp.GEOCODING:
            return Answer({"results": [{"name": params["name"], "country": "X", "latitude": 1, "longitude": 2}]})
        return Answer({"current": {"temperature_2m": 10}})

    monkeypatch.setattr(demo_webapp.requests, "get", get)
    monkeypatch.setattr(demo_webapp, "PER_SENDER", 2)
    monkeypatch.setattr(demo_webapp, "CACHE_SIZE", 2)
    demo_webapp._cache.clear()
    demo_webapp._senders.clear()
    sender = {"REMOTE_ADDR": "203.0.113.9"}
    for city in ("Aachen", "Bonn"):
        assert "°C" in client.get(f"/demo/weather?stadt={city}", environ_base=sender).data.decode()
    assert len(calls) == 4 and len(demo_webapp._cache) == 2
    page = client.get("/demo/weather?stadt=Köln", environ_base=sender).data.decode()
    assert len(calls) == 4 and "nicht erreichbar" in page            # the third new city: not asked
    assert "°C" in client.get("/demo/weather?stadt=Bonn", environ_base=sender).data.decode()  # from the cache
    assert "°C" in client.get("/demo/weather?stadt=Köln", environ_base={"REMOTE_ADDR": "198.51.100.3"}).data.decode()
    assert len(demo_webapp._cache) == 2                              # the oldest went


# --- W12: NAT64 and site-local IPv6 -------------------------------------------------------------------------------


@pytest.mark.parametrize("address", ["64:ff9b::a9fe:a9fe", "64:ff9b::808:808", "64:ff9b:1::a00:1", "fec0::1",
                                     "2002:a9fe:a9fe::1", "::ffff:10.0.0.1"])
def test_nat64_and_site_local_are_not_public(monkeypatch, address):
    assert not webservice.public(ipaddress.ip_address(address))
    monkeypatch.setattr(socket, "getaddrinfo", resolver({"api.example": address}))
    with pytest.raises(webservice.Blocked):
        webservice.fetch("GET", "https://api.example/")


def test_public_addresses_stay_public():
    assert webservice.public(ipaddress.ip_address("93.184.216.34"))
    assert webservice.public(ipaddress.ip_address("2a00:1450:4001:80b::200e"))
