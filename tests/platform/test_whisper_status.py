"""Optional transcription status: bounded counters, no job contents, no false outages."""
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from app import status_node, whisper_jobs


def reply(data, code=200):
    return SimpleNamespace(status_code=code, json=lambda: data)


def test_disabled_relay_does_not_connect(app, monkeypatch):
    app.config.update(WHISPER_JOBS=False, WHISPER_URL="http://whisper:8091")
    monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("Disabled service must not connect"))
    response = app.test_client().get("/healthz/whisper")
    assert response.status_code == 200
    assert response.json == {"state": "disabled", "queued": 0, "running": 0}
    assert response.headers["Cache-Control"] == "no-store"


def test_relay_strips_contents_and_uses_private_credential(app, monkeypatch):
    app.config.update(WHISPER_JOBS=True, WHISPER_URL="http://whisper:8091/", WHISPER_TOKEN="private-token")
    def get(url, **kwargs):
        assert url == "http://whisper:8091/v1/status"
        assert kwargs == {"headers": {"Authorization": "Bearer private-token"}, "timeout": (1, 2), "allow_redirects": False}
        return reply({"state": "ready", "queued": 2, "running": 1, "id": "private", "text": "private"})
    monkeypatch.setattr(requests, "get", get)
    response = app.test_client().get("/healthz/whisper")
    assert response.status_code == 200
    assert response.json == {"state": "busy", "queued": 2, "running": 1}


@pytest.mark.parametrize("data", [None, {"state": "busy", "queued": True, "running": 0},
    {"state": "busy", "queued": 8, "running": 1}, {"state": "busy", "queued": 0, "running": 2},
    {"state": "ready", "queued": -1, "running": 0}])
def test_invalid_relay_response_fails_without_contents(app, monkeypatch, data):
    app.config.update(WHISPER_JOBS=True, WHISPER_URL="http://whisper:8091", WHISPER_TOKEN="private")
    monkeypatch.setattr(requests, "get", lambda *a, **k: reply(data))
    response = app.test_client().get("/healthz/whisper")
    assert response.status_code == 503
    assert response.json == {"state": "unavailable", "queued": 0, "running": 0}


def test_timeout_details_are_not_public(app, monkeypatch):
    app.config.update(WHISPER_JOBS=True, WHISPER_URL="http://whisper:8091", WHISPER_TOKEN="private")
    def fail(*a, **k):
        raise requests.Timeout("private recording and credentials")
    monkeypatch.setattr(requests, "get", fail)
    assert app.test_client().get("/healthz/whisper").status_code == 503
    assert status_node.check("whisper", "http://researcher:8000/healthz/whisper")[2] == "whisper:unavailable:0:0"


@pytest.mark.parametrize("state,q,r,ok,detail", [
    ("disabled", 0, 0, True, "whisper:disabled:0:0"),
    ("busy", 2, 1, True, "whisper:ready:0:0"),            # T02-NEU-3: no use of the service in public
    ("ready", 0, 0, True, "whisper:ready:0:0"),
    ("disabled", 1, 0, False, "whisper:unavailable:0:0"),
    ("busy", 0, 2, False, "whisper:unavailable:0:0"),
    ("unavailable", 0, 0, False, "whisper:unavailable:0:0")])
def test_status_check_validates_state(monkeypatch, state, q, r, ok, detail):
    monkeypatch.setattr(requests, "get", lambda *a, **k: reply({"state": state, "queued": q, "running": r}))
    result = status_node.check("whisper", "http://researcher:8000/healthz/whisper")
    assert (result[0], result[2]) == (ok, detail)


@pytest.mark.parametrize("state,q,r,de,en,down", [
    ("disabled", 0, 0, "deaktiviert", "disabled", False),
    ("ready", 0, 0, "läuft", "running", False),          # the words of every other service (Ralf, 2026-10-19)
    ("busy", 2, 1, "läuft", "running", False),           # a row of before 2026-10-15
    ("unavailable", 0, 0, "gestört", "disrupted", True)])
def test_page_and_availability(tmp_path, monkeypatch, state, q, r, de, en, down):
    path = str(tmp_path / "status.db")
    store = status_node.Store(path)
    store.add([(int(time.time()) - 1, "whisper", int(not down), 3, f"whisper:{state}:{q}:{r}")])
    summary = status_node.summary(store, "whisper")
    assert bool(status_node.disrupted([summary], [])) == down
    assert summary["checks"] == (0 if state == "disabled" else 1)
    assert summary["share"] == (None if state == "disabled" else 0 if down else 100)
    monkeypatch.setenv("STATUS_DB", path)
    monkeypatch.setenv("STATUS_CHECKS", "whisper=whisper:http://researcher:8000/healthz/whisper")
    monkeypatch.setattr(status_node.threading.Thread, "start", lambda self: None)
    client = status_node.create().test_client()
    for lang, label in [("de", de), ("en", en)]:
        page = client.get("/", headers={"Accept-Language": lang}).get_data(as_text=True)
        assert f">{label}</span>" in page and "WhisperX" in page
        assert "Betriebsbereit" not in page and "Ready" not in page
        assert "whisper:" not in page
        # Abgleich 2026-10-15, T02-NEU-3/NB-11: the public page showed the transcription's queue
        assert "wartend" not in page and "queued" not in page and "Beschäftigt" not in page
        if state == "disabled":
            assert "status-badge status-none" in page and "Disrupted:" not in page and "Gestört:" not in page


def test_internal_network_boundary():
    root = Path(__file__).resolve().parents[2] / "deploy/docker"
    for path in [root / "compose.yaml", root / "isolation/networks.yaml"]:
        assert "whisper=whisper:http://researcher:8000/healthz/whisper," in path.read_text()
    for side in ["researcher", "participant"]:
        assert "location = /healthz/whisper { return 404; }" in (root / "nginx/locations" / f"{side}.conf").read_text()
