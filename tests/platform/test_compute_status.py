"""The status relay checks the signed compute response without opening another Docker network."""
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from app import compute, compute_node


@pytest.fixture
def relay(app, monkeypatch):
    secret = "status-test-secret" * 3
    app.config.update(COMPUTE_URL="http://compute:8090", COMPUTE_SECRET=secret)
    node = compute_node.create(secret=secret, callback="", inline=True).test_client()

    def ask(base, path, key, timeout):
        assert (base, path, key, timeout) == ("http://compute:8090", "/stand", secret, 2)
        headers = compute.signed_headers(key, path, b"", "GET")
        response = node.get(path, headers=headers)
        return SimpleNamespace(status_code=response.status_code, content=response.data, headers=response.headers,
                               compute_nonce=headers["X-Compute-Nonce"])

    monkeypatch.setattr(compute, "ask", ask)
    return app.test_client()


def test_relay_accepts_real_signed_response_and_exposes_only_health(relay):
    response = relay.get("/healthz/compute")
    assert response.status_code == 200
    assert response.json == {"status": "ok"}
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("failure", ["timeout", "http", "unsigned", "malformed", "version"])
def test_relay_reports_failure_without_details(app, relay, monkeypatch, failure):
    def ask(*args, **kwargs):
        if failure == "timeout":
            raise requests.Timeout("private address and secret")
        body = compute.pack({"version": "wrong"}) if failure == "version" else b"not gzip"
        headers = compute.signed_headers(app.config["COMPUTE_SECRET"], "/stand", body)
        return SimpleNamespace(status_code=403 if failure == "http" else 200, content=body,
                               headers={} if failure == "unsigned" else headers)

    monkeypatch.setattr(compute, "ask", ask)
    response = relay.get("/healthz/compute")
    assert response.status_code == 503
    assert response.json == {"status": "degraded"}


def test_unconfigured_compute_does_not_probe(app, relay, monkeypatch):
    app.config["COMPUTE_URL"] = ""
    monkeypatch.setattr(compute, "ask", lambda *a, **k: pytest.fail("Must not probe"))
    assert relay.get("/healthz/compute").status_code == 503


def test_status_config_keeps_network_boundary_and_blocks_public_relay():
    root = Path(__file__).resolve().parents[2] / "deploy" / "docker"
    for path in [root / "compose.yaml", root / "isolation" / "networks.yaml"]:
        assert "compute=http:http://researcher:8000/healthz/compute," in path.read_text()
    for side in ["researcher", "participant"]:
        assert "location = /healthz/compute { return 404; }" in (
            root / "nginx" / "locations" / f"{side}.conf").read_text()


# --- signature 2 (audit 2026-10-18, CFG-07) ----------------------------------------------------------------------

SECRET = "signature-test-secret" * 3


def _node():
    return compute_node.create(secret=SECRET, callback="", inline=True).test_client()


def test_a_response_of_the_node_is_no_request_of_the_other_direction(app):
    """A response the node signed for GET /stand is not valid as a request POST /rechenknecht/stand to the app (only
    the different body format stopped it before), nor as the answer to another request of the app."""
    app.config.update(COMPUTE_URL="http://compute:8090", COMPUTE_SECRET=SECRET)
    asked = compute.signed_headers(SECRET, "/stand", b"", "GET")
    r = _node().get("/stand", headers=asked)
    assert r.status_code == 200 and "X-Compute-Signature-2" in r.headers
    response = SimpleNamespace(headers=r.headers, content=r.data, compute_nonce=asked["X-Compute-Nonce"])
    assert compute.verify_response(SECRET, response, "/stand")
    other = SimpleNamespace(headers=r.headers, content=r.data, compute_nonce="0" * 32)
    assert not compute.verify_response(SECRET, other, "/stand")                    # bound to its own request
    as_request = {k: v for k, v in r.headers.items() if k.startswith("X-Compute") and k != "X-Compute-Signature"}
    app.config["COMPUTE_SIGNATURE_V1"] = False
    assert app.test_client().post("/rechenknecht/stand", data=r.data, headers=as_request).status_code == 403
    with app.test_request_context():
        assert not compute.verify(SECRET, r.headers, "/stand", r.data, method="POST")


def test_a_replayed_request_is_refused():
    """The same signed POST /auftrag a second time: 403 by its nonce, and in no case a second job."""
    node = _node()
    body = compute.pack({"job": "J" * 20, "kind": "maxdiff_hb", "version": "other", "inp": {}})
    headers = compute.signed_headers(SECRET, "/auftrag", body, "POST")
    first = node.post("/auftrag", data=body, headers=headers)
    assert first.status_code == 409                                       # another code version: computed at home
    assert node.post("/auftrag", data=body, headers=headers).status_code == 403
    # the method belongs to the signature: a signed GET is no POST
    get = compute.signed_headers(SECRET, "/auftrag", body, "GET")
    assert node.post("/auftrag", data=body, headers=get).status_code == 403


def test_signature_1_is_off_by_default_and_can_be_switched_on(monkeypatch):
    """A Rechenknecht or server of an older version signs with signature 1 only: refused by default since the audit
    of 2026-10-22 (E-02: no nonce, a request without signature 2 fell back to it), taken with
    COMPUTE_SIGNATURE_V1=1 for an outside Rechenknecht that is not updated yet (BETRIEB.md)."""
    import time

    node = _node()
    ts = str(int(time.time()))
    old = {"X-Compute-Time": ts, "X-Compute-Signature": compute.sign(SECRET, ts, "/stand", b"")}
    monkeypatch.delenv("COMPUTE_SIGNATURE_V1", raising=False)
    assert node.get("/stand", headers=old).status_code == 403
    monkeypatch.setenv("COMPUTE_SIGNATURE_V1", "1")
    assert node.get("/stand", headers=old).status_code == 200
    monkeypatch.setenv("COMPUTE_SIGNATURE_V1", "0")
    assert node.get("/stand", headers=old).status_code == 403
    assert "X-Compute-Signature" not in compute.signed_headers(SECRET, "/stand", b"", "GET")
    assert node.get("/stand", headers=compute.signed_headers(SECRET, "/stand", b"", "GET")).status_code == 200
