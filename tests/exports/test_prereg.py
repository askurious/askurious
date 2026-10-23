"""Pre-registration snapshot frozen when the fieldwork starts (app/prereg.py)."""
import hashlib
import io
import zipfile

from app.extensions import db
from app.models import Survey
from tests.conftest import fill_consent


def _start(client):
    fill_consent(client)
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "info"})
    return client.post("/surveys/1/start", data={}, follow_redirects=True)


def test_start_freezes_the_survey_once_per_version(app, client, survey):
    from app import lifecycle, prereg

    assert "Sobald die Feldphase startet" in client.get("/surveys/1/data").data.decode()
    assert "als Präregistrierung eingefroren" in _start(client).data.decode()
    with app.app_context():
        s = db.session.get(Survey, 1)
        [(name, version, _, digest)] = prereg.listing(s)
        assert version == 1
        lifecycle.close(s, "paused")
        lifecycle.start(s, None, None)                                  # a restart after a pause freezes nothing
        db.session.commit()
        assert len(prereg.listing(s)) == 1
    page = client.get("/surveys/1/data").data.decode()
    assert "Präregistrierung" in page and digest in page
    r = client.get(f"/surveys/1/export/preregistration/{name}")
    assert r.status_code == 200 and r.mimetype == "application/zip"
    body = r.data
    assert hashlib.sha256(body).hexdigest() == digest
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        names = set(z.namelist())
        assert {"README.md", "survey.json", "questionnaire.md", "codebook.csv", "checksums.sha256"} <= names
        for line in z.read("checksums.sha256").decode().splitlines():
            h, fname = line.split("  ")
            assert hashlib.sha256(z.read(fname)).hexdigest() == h
    assert client.get("/surveys/1/export/preregistration/..%2F..%2Fapp.db").status_code == 404
    assert client.get("/surveys/1/export/preregistration/v9_20260101-000000.zip").status_code == 404


def test_new_version_is_frozen_again(app, client, survey):
    from app import lifecycle, prereg

    _start(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        lifecycle.close(s, "paused")
        lifecycle.reopen_for_editing(s)
        db.session.commit()
    client.post("/surveys/1/start", data={})
    with app.app_context():
        assert [v for _, v, _, _ in prereg.listing(db.session.get(Survey, 1))] == [2, 1]


def test_freeze_failure_does_not_stop_the_start(app, client, survey, monkeypatch):
    from app import prereg

    def broken(survey):
        raise OSError("disk full")

    monkeypatch.setattr(prereg, "_files", broken)
    page = _start(client).data.decode()
    assert "Die Befragung ist gestartet." in page and "als Präregistrierung eingefroren" not in page
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.status == Survey.RUNNING and prereg.listing(s) == []


def test_snapshot_names_the_local_time_not_the_servers(app, client, survey, monkeypatch):
    """Mathe-Audit X-5: name and "eingefroren am" in Europe/Berlin, as everywhere in the app; the UTC time next to it."""
    from datetime import datetime

    from app import models, prereg

    monkeypatch.setattr(models, "utcnow", lambda: datetime(2026, 10, 1, 8, 0, 0))   # 10:00 in Berlin (CEST)
    _start(client)
    with app.app_context():
        [(name, _, at, _)] = prereg.listing(db.session.get(Survey, 1))
        assert name.startswith("v1_20261001-100000") and at == datetime(2026, 10, 1, 10, 0, 0)
    body = client.get(f"/surveys/1/export/preregistration/{name}").data
    readme = zipfile.ZipFile(io.BytesIO(body)).read("README.md").decode()
    assert "am 01.10.2026 um 10:00:00 (UTC 2026-10-01 08:00:00)" in readme


def test_a_start_that_is_not_committed_leaves_no_pre_registration(app, client, survey):
    """Code review 2026-10-15, D6: the snapshot was written and stamped before the start was committed; a start whose
    commit failed left a "pre-registration frozen at the start" of a fieldwork that never began."""
    import os

    from app import lifecycle, prereg

    fill_consent(client)
    client.post("/surveys/1/editor/blocks/2/items", data={"type": "info"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        lifecycle.start(s, None, None)
        assert prereg.listing(s) == []                                  # written, but nobody lists it yet
        db.session.rollback()                                            # the commit of the start failed
        s = db.session.get(Survey, 1)
        assert s.status == Survey.DRAFT and prereg.listing(s) == []
        assert not os.path.isdir(prereg.folder(s)) or os.listdir(prereg.folder(s)) == []
        lifecycle.start(s, None, None)
        db.session.commit()
        [(name, version, _, digest)] = prereg.listing(s)
        assert version == 1 and digest and set(os.listdir(prereg.folder(s))) == {name, name[:-4] + ".sha256"}
