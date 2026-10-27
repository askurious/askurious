"""Interviews without a network (app/interview_offline.py; concept "Konzept Offline-Modus für Interviews", Ralf
2026-10-22, E1-E8 as recommended): the feature and the guide's switch, the field pack with reserved places and their
deletion codes, the field kit page, the sync of a whole interview (once, found again by its pid), the deletion code
used before the sync, withdrawing and expiry, and the texts that name it."""
import json
from datetime import timedelta
from pathlib import Path

from app.extensions import db
from app.models import Response, Survey, utcnow
from tests.interview.test_interview import make

STATIC = Path(__file__).parents[2] / "app" / "static"
WEBM = b"\x1a\x45\xdf\xa3"


def _on(app, client, switch=True):
    code, sid = make(app, client)
    if switch:
        assert client.post(f"/guide/{code}/offline/switch", data={"on": "1"}).status_code == 302
    return code, sid


def _pack(client, code, n=3, device="dev1"):
    r = client.post(f"/guide/{code}/offline/pack", json={"n": n, "device": device})
    assert r.status_code == 200, r.data
    return r.json


def _sync(client, code, pack, slot, **extra):
    body = {"pack": pack["pack"], "device": pack["device"], "pid": slot["pid"], "date": "2026-10-22", "mode": "",
            "consent": {"form": "schriftlich", "audio": True, "all": "2026-10-22T10:00:00"}, "trial": False,
            "started": "2026-10-22T10:00:00",
            "state": {"states": {}, "ticks": [], "fits": {}, "notes": {}, "marks": [{"t": 5, "item": "", "text": "Pause"}],
                      "elapsed": 60000, "blocks": {}, "running": False}, "forms": []}
    body.update(extra)
    return client.post(f"/guide/{code}/offline/sync", json=body)


def test_off_without_the_switch(app, client):
    """No experimental feature any more (2026-10-27): only the guide's switch."""
    code, _ = make(app, client)
    page = client.get(f"/guide/{code}/interviews").get_data(as_text=True)
    assert "experimentell" not in page.split("Offline-Modus", 1)[1][:200]
    assert "Offline-Modus für diesen Leitfaden erlauben" in page and "Feldmappe öffnen" not in page
    assert client.get(f"/guide/{code}/offline").status_code == 404   # E6 a: the guide's switch too


def test_a_pack_reserves_places_with_codes_and_no_rows(app, client):
    from app import erasure

    code, sid = _on(app, client)
    pack = _pack(client, code, n=3)
    assert [s["label"] for s in pack["slots"]] == ["I01", "I02", "I03"]
    with app.app_context():
        survey = db.session.get(Survey, sid)
        assert db.session.scalar(db.select(db.func.count(Response.id)).filter_by(survey_id=sid)) == 0
        for s in pack["slots"]:
            assert s["code"] == erasure.written(survey, erasure.raw_code(survey, s["pid"]))
        stored = survey.interview["offline_packs"][0]
        assert "code" not in json.dumps(stored)                         # the codes go to the device only
    page = client.get(f"/guide/{code}/interviews").get_data(as_text=True)
    assert 'value="I04"' in page                                        # reserved identifiers are taken
    assert _pack(client, code, n=99)["slots"].__len__() == 20            # E2 a: at most 20


def test_the_field_kit_page_holds_the_guide_and_no_interview(app, client):
    code, _ = _on(app, client)
    page = client.get(f"/guide/{code}/offline").get_data(as_text=True)
    assert "data-offline" in page and "data-kit" in page and "interview-offline.js" in page
    assert "data-iv-remote" not in page and "data-audio-url" not in page and "data-iv-record" not in page
    sw = client.get("/guide/interview-sw.js").get_data(as_text=True)
    assert "isKit(" in sw and "7 * 24 * 3600 * 1000" in sw


def test_the_sync_makes_the_interview_once_and_ends_it(app, client):
    code, sid = _on(app, client)
    pack = _pack(client, code, n=2)
    slot = pack["slots"][0]
    r = _sync(client, code, pack, slot)
    assert r.status_code == 200 and r.json["done"] is False
    rid = r.json["rid"]
    assert _sync(client, code, pack, slot).json["rid"] == rid           # again: the same interview
    assert client.post(r.json["audio"] + "?part=1&seq=0&rec=off&start=0", data=WEBM + b"A",
                       content_type="audio/webm").status_code == 200
    r = _sync(client, code, pack, slot, finish=True, ended="2026-10-22T11:00:00")
    assert r.status_code == 200 and r.json["done"] is True
    with app.app_context():
        rows = db.session.scalars(db.select(Response).filter_by(survey_id=sid)).all()
        assert len(rows) == 1
        row = rows[0]
        assert row.status == Response.COMPLETED and row.pid == slot["pid"] and row.interview["label"] == "I01"
        assert row.interview["consent"]["audio"] is True and row.interview["marks"][0]["text"] == "Pause"
        assert row.interview["elapsed"] == 60 and row.erase_hash
    assert _sync(client, code, pack, {"pid": "nope"}).status_code == 404
    other = dict(pack, device="someone-else")
    assert _sync(client, code, other, pack["slots"][1]).status_code == 404


def test_no_interview_without_the_consent_tick(app, client):
    code, _ = _on(app, client)
    pack = _pack(client, code, n=1)
    r = _sync(client, code, pack, pack["slots"][0], consent={"form": "schriftlich", "audio": False})
    assert r.status_code == 400 and r.json["error"] == "consent"


def test_the_deletion_code_works_before_the_sync(app, client):
    code, sid = _on(app, client)
    pack = _pack(client, code, n=2)
    slot = pack["slots"][0]
    from app import interview_offline

    with app.app_context():
        assert interview_offline.erase_pending(slot["code"]) is not None
    r = _sync(client, code, pack, slot)
    assert r.status_code == 410 and r.json["error"] == "erased"        # the device deletes its copy
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id)).filter_by(survey_id=sid)) == 0
    r = client.post(f"/guide/{code}/interviews/deletion-code", data={"term": pack["slots"][1]["code"]}, follow_redirects=True)
    assert "zum Löschen vorgemerkt" in r.get_data(as_text=True)


def test_withdrawn_and_expired_packs(app, client):
    from app import interview_offline

    code, sid = _on(app, client)
    pack = _pack(client, code, n=2)
    assert client.post(f"/guide/{code}/offline/packs/{pack['pack']}/withdraw").status_code == 302
    r = _sync(client, code, pack, pack["slots"][0])
    assert r.status_code == 409 and r.json["error"] == "withdrawn"
    pack2 = _pack(client, code, n=1)
    with app.app_context():
        survey = db.session.get(Survey, sid)
        assert interview_offline.reserved_labels(survey) == {pack2["slots"][0]["label"]}  # a withdrawn pack holds none
        assert interview_offline.run(utcnow() + timedelta(days=8)) == 2   # E5 b: seven days, then gone
        db.session.refresh(survey)
        assert interview_offline.packs(survey) == []
    assert _sync(client, code, pack2, pack2["slots"][0]).status_code == 404


def test_texts_name_the_offline_mode(app, client):
    privacy = (Path(__file__).parents[2] / "app" / "templates" / "public" / "privacy.html").read_text(encoding="utf-8")
    assert "Im Offline-Modus" in privacy and "In offline mode" in privacy
    from app import privacy_concept

    with app.test_request_context():
        text = json.dumps(privacy_concept.risks(), ensure_ascii=False)
    assert "Offline-Modus" in text
    js = (STATIC / "interview-offline.js").read_text(encoding="utf-8")
    assert "PBKDF2" in js and "AES-GCM" in js and "ROUNDS = 600000" in js
    iv = (STATIC / "interview.js").read_text(encoding="utf-8")
    assert "if (OM) { AskuriousOffline.keep(st); return; }" in iv
