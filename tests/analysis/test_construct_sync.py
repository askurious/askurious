"""Sync with the construct catalogue (app/construct_sync.py, Administration > Konstrukte > Katalog): a signed manifest
is shown first and then taken; it becomes the active catalogue (construct_catalog.active()), the toolbox follows it,
going back pins the release before. Signed here with a key of the test's own (never the catalogue's)."""
import base64
import copy
import hashlib
import json
from datetime import datetime

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from app import construct_catalog as catalog
from app import construct_sync, constructs
from app.extensions import db
from app.models import Construct, ConstructSync


@pytest.fixture
def signer(monkeypatch):
    """sign(manifest) -> (bytes, signature text), with a key the catalogue's verification is set to for the test."""
    secret = Ed25519PrivateKey.generate()
    key_id = b"\x11" * 8
    raw = secret.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    monkeypatch.setattr(catalog, "PUBLIC_KEY", base64.b64encode(b"Ed" + key_id + raw).decode())

    def sign(manifest, comment=None):
        data = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
        comment = comment or f"askurious-constructs {manifest['release']}"
        sig = secret.sign(hashlib.blake2b(data, digest_size=64).digest())
        glob = secret.sign(sig + comment.encode())
        text = (f"untrusted comment: test\n{base64.b64encode(b'ED' + key_id + sig).decode()}\n"
                f"trusted comment: {comment}\n{base64.b64encode(glob).decode()}\n")
        return data, text

    yield sign
    catalog.invalidate()


def _next_release():
    """The snapshot with: kamb's question corrected (2.1), a new open construct, aub withdrawn, a new licensed one."""
    m = copy.deepcopy(catalog.manifest())
    m["release"], m["commit"] = "2026.12.0", "a" * 40
    kamb = next(c for c in m["constructs"] if c["key"] == "kamb")
    kamb["version"] = "2.1"
    kamb["versions"].insert(0, {"version": "2.1"})
    kamb["wording"]["de"]["question"] = "Bewerten Sie den Beitrag mithilfe der folgenden Begriffspaare."
    kamb["hash"]["wording"]["de"] = catalog.wording_hash(kamb["wording"]["de"])
    age = next(c for c in m["constructs"] if c["key"] == "age")
    new = copy.deepcopy(age)
    new.update(key="alter2", id="ask:alter2", version="1.0", versions=[{"version": "1.0"}])
    new["name"] = {"de": "Alter, zweite Fassung", "en": "Age, second version"}
    m["constructs"].append(new)
    aub = next(c for c in m["constructs"] if c["key"] == "aub")
    aub["status"] = "retired"
    asku = next(c for c in m["constructs"] if c["key"] == "asku")
    lic = copy.deepcopy(asku)
    lic.update(key="lizenz2", id="ask:lizenz2", licence={**asku["licence"], "permission_required": True})
    m["constructs"].append(lic)
    return m


def test_preview_then_take_a_newer_release(app, signer):
    with app.app_context():
        data, sig = signer(_next_release())
        row = construct_sync.prepare(data, sig, "upload", "test")
        assert row.status == ConstructSync.PREVIEW
        kinds = {c["key"]: c["kind"] for c in row.changes}
        assert kinds == {"kamb": "wording", "alter2": "new", "aub": "retired", "lizenz2": "new"}
        assert catalog.release()[0] == "2026.11.0"  # a preview changes nothing yet
        assert "lizenz2" not in constructs.NOT_SHARED
        construct_sync.apply(row)
        assert catalog.release()[0] == "2026.12.0"
        assert "lizenz2" in constructs.NOT_SHARED and "lizenz2" in constructs.INSTRUMENTS
        kamb = constructs.get("kamb")
        assert kamb.version == "2.1" and kamb.catalog_release == "2026.12.0" and kamb.source == "catalog"
        assert kamb.spec["texts"]["de"]["question"].startswith("Bewerten Sie den Beitrag")
        assert constructs.get("alter2") is not None
        aub = db.session.scalar(db.select(Construct).filter_by(key="aub"))
        assert aub.visibility == "retired" and constructs.get("aub") is None  # kept for surveys, out of the toolbox
        assert constructs.find("aub") is not None
        assert "aub" not in [c.key for c in constructs.search()]


def test_a_construct_missing_from_a_release_is_kept_as_withdrawn(app, signer):
    with app.app_context():
        m = copy.deepcopy(catalog.manifest())
        m["release"] = "2026.12.0"
        m["constructs"] = [c for c in m["constructs"] if c["key"] != "kamb"]
        row = construct_sync.prepare(*signer(m), "upload")
        assert {c["key"]: c["kind"] for c in row.changes} == {"kamb": "retired"}
        assert next(c for c in row.manifest["constructs"] if c["key"] == "kamb")["status"] == "retired"


def test_a_bad_signature_takes_nothing(app, signer):
    with app.app_context():
        data, sig = signer(_next_release())
        with pytest.raises(construct_sync.SyncError, match="Signatur ungültig"):
            construct_sync.prepare(data + b" ", sig, "upload")
        with pytest.raises(construct_sync.SyncError):
            construct_sync.prepare(data, "", "upload")
        assert catalog.release()[0] == "2026.11.0"
        rows = db.session.scalars(db.select(ConstructSync)).all()
        assert [r.status for r in rows] == ["failed", "failed"] and all(r.manifest is None for r in rows)


def test_licensed_wording_in_a_manifest_is_refused(app, signer):
    with app.app_context():
        m = _next_release()
        next(c for c in m["constructs"] if c["key"] == "asku")["wording"] = {"de": {"question": "x"}}
        with pytest.raises(construct_sync.SyncError, match="asku"):
            construct_sync.prepare(*signer(m), "upload")


def test_going_back_pins_the_release_before(app, signer):
    with app.app_context():
        first = {**copy.deepcopy(catalog.manifest()), "release": "2026.11.1"}  # the same constructs, a new release
        construct_sync.apply(construct_sync.prepare(*signer(first), "upload"))
        construct_sync.apply(construct_sync.prepare(*signer(_next_release()), "upload"))
        assert constructs.get("kamb").version == "2.1"
        row = construct_sync.undo()
        assert row.release == "2026.11.1" and catalog.release()[0] == "2026.11.1"
        assert construct_sync.pin() == "2026.11.1" and construct_sync.pinned_by_setting()
        kamb = constructs.get("kamb")
        assert kamb.version == "2.0" and kamb.spec["texts"]["de"]["question"].startswith("Bewerten Sie den Artikel")
        aub = db.session.scalar(db.select(Construct).filter_by(key="aub"))
        assert aub.visibility == "public"  # back in the toolbox
        construct_sync.unpin()
        assert construct_sync.pin() is None


def test_fetching_from_a_folder_respects_the_pin(app, signer, tmp_path):
    with app.app_context():
        data, sig = signer(_next_release())
        (tmp_path / "manifest.json").write_bytes(data)
        (tmp_path / "manifest.json.sig").write_text(sig, encoding="utf-8")
        app.config.update(CONSTRUCTS_SOURCE=str(tmp_path), CONSTRUCTS_PIN="2026.11.0")
        try:
            with pytest.raises(construct_sync.SyncError, match="2026.11.0"):
                construct_sync.check()
            app.config["CONSTRUCTS_PIN"] = ""
            row = construct_sync.check()
            assert row.status == ConstructSync.PREVIEW and row.source.endswith("manifest.json")
        finally:
            app.config.update(CONSTRUCTS_SOURCE="off", CONSTRUCTS_PIN="")


def test_the_same_release_again_changes_nothing(app, signer):
    with app.app_context():
        row = construct_sync.prepare(*signer(copy.deepcopy(catalog.manifest())), "upload")
        assert row.status == ConstructSync.UNCHANGED and row.release == "2026.11.0" and row.manifest is None


def test_off_fetches_nothing(app):
    with app.app_context():
        with pytest.raises(construct_sync.SyncError, match="abgeschaltet"):
            construct_sync.check()


def test_redirects_are_followed_and_each_checked(app, monkeypatch):
    from app import webservice

    calls = []

    def fake(method, url, body=None, headers=None, timeout=None, max_bytes=None):
        calls.append(url)
        if "github.com" in url:
            return 302, {"location": "https://objects.example/file"}, b""
        return 200, {}, b"content"

    monkeypatch.setattr(webservice, "fetch_full", fake)
    with app.app_context():
        assert construct_sync._get("https://github.com/x/manifest.json", 100) == b"content"
        assert calls == ["https://github.com/x/manifest.json", "https://objects.example/file"]
    monkeypatch.setattr(webservice, "fetch_full", lambda *a, **k: (302, {"location": "https://github.com/again"}, b""))
    with app.app_context(), pytest.raises(construct_sync.SyncError, match="Weiterleitungen"):
        construct_sync._get("https://github.com/x", 100)


def test_nightly_once_a_night_and_only_outside_requests(app, signer, tmp_path):
    with app.app_context():
        data, sig = signer(_next_release())
        (tmp_path / "manifest.json").write_bytes(data)
        (tmp_path / "manifest.json.sig").write_text(sig, encoding="utf-8")
        app.config["CONSTRUCTS_SOURCE"] = str(tmp_path)
        try:
            noon = datetime(2026, 11, 10, 11, 0)   # 12:00 in Berlin
            night = datetime(2026, 11, 10, 2, 0)   # 03:00 in Berlin
            assert construct_sync.nightly(noon) is None
            row = construct_sync.nightly(night)
            assert row.status == ConstructSync.APPLIED and row.trigger == "auto"
            assert construct_sync.nightly(night) is None  # once a night
        finally:
            app.config["CONSTRUCTS_SOURCE"] = "off"
    with app.test_request_context("/surveys/1"):
        assert construct_sync.nightly(datetime(2026, 11, 11, 2, 0)) is None  # never in someone's request


def test_admin_page_and_preview(app, client, signer):
    page = client.get("/admin/constructs").data.decode()
    assert 'id="katalog"' in page and "2026.11.0" in page and "im Programm enthalten" in page
    assert "Manifest-Datei hochladen" in page and "Jetzt abgleichen" not in page  # CONSTRUCTS_SOURCE=off in the tests
    with app.app_context():
        data, sig = signer(_next_release())
    from io import BytesIO

    answer = client.post("/admin/constructs/catalog", data={"action": "upload", "manifest": (BytesIO(data), "manifest.json"),
                                                             "signature": (BytesIO(sig.encode()), "manifest.json.sig")},
                         content_type="multipart/form-data")
    assert answer.status_code == 302 and "/admin/constructs/catalog/" in answer.headers["Location"]
    preview = client.get(answer.headers["Location"]).data.decode()
    assert "Release 2026.12.0 übernehmen?" in preview and "zurückgezogen" in preview and "Wortlaut" in preview
    taken = client.post(answer.headers["Location"])
    assert taken.status_code == 302
    page = client.get("/admin/constructs").data.decode()
    assert "Geändert mit 2026.12.0" in page and "Signatur geprüft" in page and "Rückgängig auf" not in page
    bad = client.post("/admin/constructs/catalog", data={"action": "upload", "manifest": (BytesIO(data + b" "), "m.json"),
                                                          "signature": (BytesIO(sig.encode()), "m.json.sig")},
                      content_type="multipart/form-data", follow_redirects=True).data.decode()
    assert "Signatur ungültig" in bad
