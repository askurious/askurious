"""Rechenknecht (app/compute.py, app/compute_node.py): estimations on a second machine, prepared and off by default.
The second machine is the node's Flask app in this process; requests.post/get to its address go to its test client."""
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
import requests

from app import compute, compute_node
from app.extensions import db
from app.models import Item
from tests.analysis.test_maxdiff_hb import with_dummies

SECRET = "s" * 40
NODE = "https://rechner.example.org"


class _Answer:
    def __init__(self, r):
        self.status_code, self.content, self.headers = r.status_code, r.data, r.headers
        self.text = r.get_data(as_text=True) if r.mimetype != "application/gzip" else ""


@pytest.fixture
def node(app, monkeypatch):
    """The Rechenknecht (computing in the request) behind NODE; calls back to nothing (the server asks itself)."""
    box = {"app": compute_node.create(secret=SECRET, callback="", inline=True)}
    sent = []

    def post(url, data=None, headers=None, timeout=None):
        assert url.startswith(NODE + "/"), url
        sent.append(compute.unpack(data))
        return _Answer(box["app"].test_client().post(url[len(NODE):], data=data, headers=headers))

    def get(url, headers=None, timeout=None):
        assert url.startswith(NODE + "/"), url
        return _Answer(box["app"].test_client().get(url[len(NODE):], headers=headers))

    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(requests, "get", get)
    app.config.update(COMPUTE_URL=NODE, COMPUTE_SECRET=SECRET)
    box["sent"] = sent
    return box


def _join(prefix):
    for t in threading.enumerate():
        if t.name.startswith(prefix):
            t.join(120)


def _quiet(app, item_id, seconds, module="maxdiff_hb"):
    """Make the marker of a job on the Rechenknecht look silent for that long."""
    import importlib

    from app import maxdiff_hb

    with app.app_context():
        path = importlib.import_module(f"app.{module}")._paths(db.session.get(Item, item_id))[1]
        marker = maxdiff_hb._read(path)
        then = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
        marker.update(beat=then, started=then)
        marker.pop("asked", None)
        maxdiff_hb._write(path, marker)
        return marker


def _stored(app, item_id=2):
    from app import maxdiff_hb

    with app.app_context():
        return maxdiff_hb.load(db.session.get(Item, item_id))


def test_signature_and_packing():
    body = compute.pack({"a": [1, 2]})
    h = compute.signed_headers(SECRET, "/auftrag", body)
    assert compute.verify(SECRET, h, "/auftrag", body) and compute.unpack(body) == {"a": [1, 2]}
    assert not compute.verify("x" * 40, h, "/auftrag", body)                        # another key
    assert not compute.verify(SECRET, h, "/stand", body)                            # another route
    assert not compute.verify(SECRET, h, "/auftrag", body + b"x")                   # changed on the way
    old = {**h, "X-Compute-Time": str(int(time.time()) - compute.MAX_AGE - 5)}
    old["X-Compute-Signature"] = compute.sign(SECRET, old["X-Compute-Time"], "/auftrag", body)
    assert not compute.verify(SECRET, old, "/auftrag", body)                        # too old: no replay
    assert not compute.verify("", h, "/auftrag", body)
    with pytest.raises(ValueError):
        compute.unpack(b"not gzip")


def test_pseudonym_and_back():
    inp = {"key": "k", "partial": "/srv/x.part.json", "persons": [{"pid": 17, "v": 0}, {"pid": 4, "v": 1}]}
    sent, real = compute.pseudonym(inp)
    assert [p["pid"] for p in sent["persons"]] == [1, 2] and real == [17, 4] and "partial" not in sent
    assert inp["persons"][0]["pid"] == 17                                         # the input itself unchanged
    assert compute.restore({"pids": [2, 1]}, real)["pids"] == [4, 17]


def test_off_without_settings(app, client, survey, monkeypatch):
    with_dummies(app, client, n=12)
    app.config["HB_SYNC"] = False

    def no_network(*a, **k):
        raise AssertionError("no Rechenknecht is set up")

    monkeypatch.setattr(requests, "post", no_network)
    client.post("/surveys/1/results/hb/2", data={"source": "dummy", "speeder": "50"})
    _join("hb-item-2")
    assert _stored(app)["pids"]
    page = client.get("/admin/database").get_data(as_text=True)
    assert "Rechenknecht" in page and "Nicht eingerichtet" in page


def test_estimate_on_the_node_equals_the_local_one(app, client, survey, node):
    with_dummies(app, client, n=15)
    app.config["COMPUTE_URL"] = ""
    client.post("/surveys/1/results/hb/2", data={"source": "dummy", "speeder": "50"})       # here, HB_SYNC
    local = _stored(app)
    with app.app_context():
        from app import maxdiff_hb

        os.remove(maxdiff_hb._paths(db.session.get(Item, 2))[0])
    app.config.update(COMPUTE_URL=NODE, HB_SYNC=False)
    r = client.post("/surveys/1/results/hb/2", data={"source": "dummy", "speeder": "50"},
                    headers={"X-Requested-With": "fetch"})
    assert r.get_json()["state"] == "started"
    job = node["sent"][-1]
    assert job["kind"] == "maxdiff_hb" and job["version"] == compute.version()
    assert [p["pid"] for p in job["inp"]["persons"]] == list(range(1, len(local["pids"]) + 1))  # no real numbers
    assert not [t for t in threading.enumerate() if t.name == "hb-item-2"]                       # nothing computed here
    assert client.get("/surveys/1/results/hb/2/status").get_json()["state"] == "running"         # heard just now
    _quiet(app, 2, compute.QUIET + 5)
    assert client.get("/surveys/1/results/hb/2/status").get_json() == {"state": "done"}         # asked and fetched
    remote = _stored(app)
    assert remote["pids"] == local["pids"] and remote["util"] == local["util"] and remote["key"] == local["key"]
    assert remote["pool"] == local["pool"]
    assert not os.listdir(os.path.join(app.config["UPLOAD_FOLDER"], "hb", "_remote"))           # index gone


def test_result_pushed_by_the_node(app, client, survey, node):
    with_dummies(app, client, n=12)
    app.config["HB_SYNC"] = False
    client.post("/surveys/1/results/hb/2", data={"source": "dummy", "speeder": "50"})
    job = node["sent"][-1]["job"]
    result = compute_node._work("maxdiff_hb", node["sent"][-1]["inp"], job, "", SECRET)["result"]
    anon = client.application.test_client()                                        # the node is not signed in

    def push(payload, secret=SECRET, path="/ergebnis"):
        body = compute.pack(payload)
        return anon.post("/rechenknecht" + path, data=body, headers=compute.signed_headers(secret, path, body))

    assert push({"job": job, "result": result}, secret="x" * 40).status_code == 403
    assert push({"job": "A" * 24, "result": result}).status_code == 404            # a job this server never sent
    assert push({"job": job, "result": {**result, "key": "other"}}).status_code == 404   # not its input
    assert _stored(app) is None
    assert push({"job": job, "progress": 0.5}, path="/stand").status_code == 200
    assert client.get("/surveys/1/results/hb/2/status").get_json()["progress"] == 0.5
    assert push({"job": job, "result": result}).status_code == 200
    assert client.get("/surveys/1/results/hb/2/status").get_json() == {"state": "done"}
    assert len(_stored(app)["pids"]) == len(result["pids"])
    assert push({"job": job, "result": result}).status_code == 404                 # delivered once only


def test_node_error_is_shown(app, client, survey, node):
    with_dummies(app, client, n=12)
    app.config["HB_SYNC"] = False
    client.post("/surveys/1/results/hb/2", data={"source": "dummy", "speeder": "50"})
    job = node["sent"][-1]["job"]
    body = compute.pack({"job": job, "error": "ValueError: kaputt"})
    client.application.test_client().post("/rechenknecht/ergebnis", data=body,
                                          headers=compute.signed_headers(SECRET, "/ergebnis", body))
    assert client.get("/surveys/1/results/hb/2/status").get_json()["state"] == "error"


@pytest.mark.parametrize("trouble", ["unreachable", "version"])
def test_computed_here_when_the_node_cannot(app, client, survey, node, monkeypatch, trouble):
    with_dummies(app, client, n=12)
    app.config["HB_SYNC"] = False
    if trouble == "unreachable":
        def down(*a, **k):
            raise requests.ConnectionError("down")

        monkeypatch.setattr(requests, "post", down)
    else:  # the node has another version of the estimation code
        refused = compute_node.create(secret=SECRET, callback="", inline=True).test_client()

        def other(url, data=None, headers=None, timeout=None):
            r = refused.post("/auftrag", data=compute.pack({**compute.unpack(data), "version": "an-older-one"}),
                             headers={})
            assert r.status_code == 403
            body = compute.pack({**compute.unpack(data), "version": "an-older-one"})
            return _Answer(refused.post("/auftrag", data=body, headers=compute.signed_headers(SECRET, "/auftrag", body)))

        monkeypatch.setattr(requests, "post", other)
    client.post("/surveys/1/results/hb/2", data={"source": "dummy", "speeder": "50"})
    _join("hb-item-2")
    assert _stored(app)["pids"]
    assert not os.listdir(os.path.join(app.config["UPLOAD_FOLDER"], "hb", "_remote"))


def test_lost_job_is_computed_here(app, client, survey, node):
    with_dummies(app, client, n=12)
    app.config["HB_SYNC"] = False
    client.post("/surveys/1/results/hb/2", data={"source": "dummy", "speeder": "50"})
    node["app"] = compute_node.create(secret=SECRET, callback="", inline=True)    # restarted: knows nothing
    _quiet(app, 2, compute.QUIET + 5)
    assert client.get("/surveys/1/results/hb/2/status").get_json()["state"] == "running"
    _join("hb-local")
    assert client.get("/surveys/1/results/hb/2/status").get_json() == {"state": "done"}
    assert _stored(app)["pids"]


def test_silent_node_is_given_up(app, client, survey, node, monkeypatch):
    with_dummies(app, client, n=12)
    app.config["HB_SYNC"] = False
    client.post("/surveys/1/results/hb/2", data={"source": "dummy", "speeder": "50"})

    def down(*a, **k):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(requests, "get", down)
    _quiet(app, 2, compute.QUIET + 5)
    assert client.get("/surveys/1/results/hb/2/status").get_json()["state"] == "running"   # asked, waits
    assert not [t for t in threading.enumerate() if t.name == "hb-local"]
    _quiet(app, 2, compute.GIVE_UP + 5)
    client.get("/surveys/1/results/hb/2/status")
    _join("hb-local")
    assert client.get("/surveys/1/results/hb/2/status").get_json() == {"state": "done"}


def test_latent_classes_and_conjoint_kinds_run_on_the_node(app):
    """Every kind's run() works without the Flask app (the node has none)."""
    assert set(compute.KINDS) == {"maxdiff_hb", "conjoint_hb", "paired_hb", "cva_hb", "segments", "sem", "omega", "sem_power",
                                 "panel", "sem_fit"}
    for kind in compute.KINDS:
        assert callable(compute.runner(kind))


def test_node_refuses(node):
    c = node["app"].test_client()
    body = compute.pack({"job": "A" * 24, "kind": "maxdiff_hb", "version": compute.version(), "inp": {}})
    assert c.post("/auftrag", data=body).status_code == 403                                   # unsigned
    assert c.post("/auftrag", data=body, headers=compute.signed_headers("x" * 40, "/auftrag", body)).status_code == 403
    for bad in ({"job": "short", "kind": "maxdiff_hb", "version": compute.version(), "inp": {}},
                {"job": "A" * 24, "kind": "os.system", "version": compute.version(), "inp": {}}):
        b = compute.pack(bad)
        assert c.post("/auftrag", data=b, headers=compute.signed_headers(SECRET, "/auftrag", b)).status_code == 400
    old = compute.pack({"job": "A" * 24, "kind": "maxdiff_hb", "version": "old", "inp": {}})
    r = c.post("/auftrag", data=old, headers=compute.signed_headers(SECRET, "/auftrag", old))
    assert r.status_code == 409 and compute.unpack(r.data)["version"] == compute.version()
    assert c.get("/stand").status_code == 403
    # signature 2 covers the method (audit 2026-10-18, CFG-07): a GET signed as POST is refused like a stranger's
    assert c.get("/stand", headers=compute.signed_headers(SECRET, "/stand", b"")).status_code == 403
    h = compute.signed_headers(SECRET, "/stand", b"", "GET")
    r = c.get("/stand", headers=h)
    assert r.status_code == 200 and compute.verify(SECRET, r.headers, "/stand", r.data, method="GET",
                                                   direction=compute.RESPONSE, reply_to=h["X-Compute-Nonce"])
    assert compute.unpack(r.data)["version"] == compute.version()
    path = "/auftrag/" + "B" * 24
    assert c.get(path, headers=compute.signed_headers(SECRET, path, b"", "GET")).status_code == 404


def test_admin_section(app, client, node):
    page = client.get("/admin/database").get_data(as_text=True)
    assert "Erreichbar" in page and "gleich wie hier" in page and compute.version() in page
    app.config["COMPUTE_URL"] = "https://elsewhere.example.org"
    page = client.get("/admin/database").get_data(as_text=True)
    assert "<strong>nein</strong>" in page


def test_latent_classes_in_worker_processes_of_the_node(app, client, survey, node):
    """The node as on the second machine: a pool of processes; CBC segments with their real (string) pids."""
    from tests.analysis.test_segments import cbc_with_segments

    cbc_with_segments(app, client, n=60)
    client.post("/surveys/1/results/lc/2", data={"source": "real"})                          # here, LC_SYNC
    with app.app_context():
        from app import segments

        local = segments.load(db.session.get(Item, 2))
        os.remove(segments._paths(db.session.get(Item, 2))[0])
    node["app"] = compute_node.create(secret=SECRET, callback="", parallel=1)
    app.config["LC_SYNC"] = False
    r = client.post("/surveys/1/results/lc/2", data={"source": "real"}, headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200
    job = node["sent"][-1]
    assert job["kind"] == "segments" and all(isinstance(p["pid"], int) for p in job["inp"]["persons"])
    ask = node["app"].test_client()
    path = f"/auftrag/{job['job']}"
    for _ in range(600):
        state = compute.unpack(ask.get(path, headers=compute.signed_headers(SECRET, path, b"", "GET")).data)["state"]
        if state in ("done", "error"):
            break
        time.sleep(0.2)
    assert state == "done"
    _quiet(app, 2, compute.QUIET + 5, module="segments")
    assert client.get("/surveys/1/results/lc/2/status").get_json()["state"] == "done"
    with app.app_context():
        remote = segments.load(db.session.get(Item, 2))
    assert remote["pids"] == local["pids"] and remote["suggested"] == local["suggested"]
    assert [s["ll"] for s in remote["solutions"]] == [s["ll"] for s in local["solutions"]]
