import csv
import io
import re

from app import export
from app.extensions import db
from app.models import Response, Survey
from tests.conftest import CONSENT_OK
from tests.participation.test_panel import build, configure, start


def _rows(text):
    return list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))


def test_public_export_leaves_out_identifiers(app, client, survey):
    configure(client)
    build(app, client)
    uid = start(app, client)
    p = app.test_client()
    p.get(f"/s/{uid}?PID=prolific-123&group=chemie")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    p.post(f"/s/{uid}", data={"nav": "next"})
    p.post(f"/s/{uid}", data={"nav": "next", "i4": "ich@example.org"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.paradata = True
        r = db.session.scalar(db.select(Response).filter_by(source="real"))
        r.paradata = {"device": "mobile", "os": "Android", "browser": "Chrome"}
        db.session.commit()
        full = _rows(export.to_csv(s))[0]
        public = _rows(export.to_csv(s, public=True))[0]
        book = {row["variable"]: row for row in _rows(export.codebook_csv(s, public=True))}
        readme = export.readme_md(s)
    assert full["url_PID"] == "prolific-123" and full["pid"] and full["device"] == "mobile"
    assert not {"pid", "url_PID", "device", "os", "browser"} & set(public)
    assert not {"pid", "url_PID", "device"} & set(book)
    assert re.fullmatch(r"\d{4}-\d{2}", public["started"]) and re.fullmatch(r"\d{4}-\d{2}", public["finished"])
    assert book["started"]["notes"] == "nur Monat (JJJJ-MM)"
    assert public["duration_s"] == full["duration_s"] and public["resp_id"] == "1"
    assert "not included here" in readme and "Free-text answers" in readme
    # free text only with the explicit choice
    assert full["mail"] == "ich@example.org" and "mail" not in public and "mail" not in book
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert _rows(export.to_csv(s, public=True, open_answers=True))[0]["mail"] == "ich@example.org"
        assert "Free-text answers" not in export.readme_md(s, open_answers=True)


def test_zenodo_upload_open_answers_checkbox(app, client, survey, monkeypatch):
    from app import zenodo

    seen = []
    monkeypatch.setattr(zenodo, "upload", lambda s, c, open_answers=False: seen.append(open_answers) or {"id": 1})
    from app import zenodo_auth

    monkeypatch.setattr(zenodo_auth, "available", lambda target: True)          # connected: the only way to upload
    monkeypatch.setattr(zenodo_auth, "access_token", lambda user, target: "t")
    monkeypatch.setattr(zenodo_auth, "connection", lambda user, target: object())
    configure(client)
    build(app, client)
    with app.app_context():  # an explicit licence, without which no deposit is made (zenodo.license_id)
        consent = db.session.get(Survey, 1).consent_item
        consent.config = {**consent.config, "license": "CC BY 4.0"}
        db.session.commit()
    assert "include_open_answers" in client.get("/surveys/1/data").data.decode()  # the survey has a free-text item
    data = {"target": "sandbox", "action": "upload", "confirm_no_open_data": "1"}
    client.post("/surveys/1/zenodo", data=data)
    client.post("/surveys/1/zenodo", data={**data, "include_open_answers": "1"})
    assert seen == [False, True]


def test_zenodo_package_uses_public_export(app, client, survey, monkeypatch):
    from app import zenodo

    calls = []
    monkeypatch.setattr(export, "to_csv", lambda *a, **kw: calls.append(("data", kw.get("public"))) or "")
    monkeypatch.setattr(export, "codebook_csv", lambda *a, **kw: calls.append(("codebook", kw.get("public"))) or "")
    with app.app_context():
        zenodo.package(db.session.get(Survey, 1))
    assert calls == [("data", True), ("codebook", True)]


def test_safe_text_neutralizes_formulas():
    assert export.safe_text("=HYPERLINK(\"https://x\")") == "'=HYPERLINK(\"https://x\")"
    for text in ("+cmd", "-2+3", "@SUM(A1)", "\tx", "- ein Punkt"):
        assert export.safe_text(text) == "'" + text
    for value in ("-3", "+1.5", "-2,5", "gut", "", 7, -2.5, None):
        assert export.safe_text(value) == value


def test_comment_export_neutralizes_formulas(app, client, survey):
    r = client.post("/surveys/1/editor/preview/comment", data={"block_id": 1, "text": "=1+1", "page": 1})
    assert r.status_code == 200
    rows = _rows(client.get("/surveys/1/export/comments.csv").data.decode("utf-8"))
    assert rows[0]["text"] == "'=1+1"


def test_open_columns_cover_map_positions_not_counts(app, client, survey):
    from tests.conftest import add

    add(client, "map_marker")
    add(client, "text_input")
    with app.app_context():
        s = db.session.get(Survey, 1)
        m = next(i for b in s.blocks for i in b.items if i.type == "map_marker")
        t = next(i for b in s.blocks for i in b.items if i.type == "text_input")
        cols = export.open_columns(s)
        assert {f"{m.code}_1_lat", f"{m.code}_1_lon", f"{m.code}_json", t.code} <= cols
        assert f"{m.code}_n" not in cols and f"{m.code}_spread_m" not in cols
        t.config = {**t.config, "validation": "integer"}
        assert t.code not in export.open_columns(s)
