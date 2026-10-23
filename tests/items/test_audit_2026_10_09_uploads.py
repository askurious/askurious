"""Security and performance audit 2026-10-09, package P5 (uploads):
D1 unfinished video and audio uploads (.parts) count toward the survey's room, one open upload per element and value,
   pieces of a slot one at a time, old pieces swept; copying a question and duplicating a survey keep the room;
D2 a transcript .docx is read as a stream with small limits (no python-docx tree of millions of nodes);
D3 the ZIPs of voice messages and comment images are built in a temporary file, not in memory."""
import io
import os
import time
import zipfile

import pytest

from app import media
from app.extensions import db
from app.models import Item, Survey
from tests.conftest import add, fill_consent
from tests.analysis.test_stimulus import E, MP4, _upload, switch_on

MB = 1024 * 1024
HEAD = b"\x00\x00\x00\x18ftypisom"


def _piece(client, item, upload, index, data, **extra):
    return client.post(f"{E}/items/{item}/media", data={"upload": upload, "index": str(index),
                                                        "file": (io.BytesIO(data), "a.mp4"), **extra},
                       content_type="multipart/form-data")


def _parts_folder(app):
    with app.app_context():
        return os.path.join(app.config["UPLOAD_FOLDER"], db.session.get(Survey, 1).uid, ".parts")


def _parts_size(app):
    folder = _parts_folder(app)
    return sum(os.path.getsize(os.path.join(folder, f)) for f in os.listdir(folder))


# --- D1 ------------------------------------------------------------------------------------------------------------


def test_d1_unfinished_uploads_count_and_a_new_one_replaces_the_old(app, client, survey):
    """The repro of the audit (dateien/test_d_media_parts.py): ten uploads of 1.5 MB never finished filled .parts with
    15 MB under a room of 3 MB. Now a new upload of the same element gives up the one before, and pieces of other
    elements count toward the room."""
    app.config["MEDIA_MAX_MB"] = 2
    app.config["MEDIA_SURVEY_MAX_MB"] = 3
    switch_on(app)
    fill_consent(client)
    for _ in range(3):
        add(client, "media")                                                      # items 2, 3, 4
    piece = HEAD + b"\x00" * (MB + MB // 2 - len(HEAD))                            # 1.5 MB
    for n in range(10):
        assert _piece(client, 2, f"{n:032x}", 0, piece).status_code == 200
    assert _parts_size(app) == len(piece)                                         # one open upload for item 2
    assert _piece(client, 3, "a" * 32, 0, piece).status_code == 200              # 3 MB of the room taken
    r = _piece(client, 4, "b" * 32, 0, piece)
    assert r.status_code == 400 and "keinen Platz" in r.json["error"]
    assert _parts_size(app) == 2 * len(piece)
    # a finished upload of item 2 takes the place of its open one: it now counts as a file, no longer as a piece
    r = _piece(client, 2, "c" * 32, 0, piece, last="1", seconds="5", kind="video")
    assert r.status_code == 200, r.json
    assert _parts_size(app) == len(piece)
    with app.app_context():
        assert media.media_bytes(db.session.get(Survey, 1)) == 2 * len(piece)


def test_d1_a_value_of_the_loop_has_its_own_slot(app):
    item_a, item_b = Item(id=7), Item(id=8)
    assert media.part_slot(item_a, "") != media.part_slot(item_a, "robot") != media.part_slot(item_b, "robot")
    assert media.part_slot(item_a, "../../x") .startswith("7-") and "/" not in media.part_slot(item_a, "../../x")


def test_d1_one_piece_of_a_slot_at_a_time(app, client, survey):
    """Two requests with the same index both passed the check of the order and both appended. A lock file per slot
    lets one through; one left by a process that died goes after a minute."""
    switch_on(app)
    fill_consent(client)
    add(client, "media")
    with app.app_context():
        slot = media.part_slot(db.session.get(Item, 2), "")
    assert _piece(client, 2, "c" * 32, 0, MP4).status_code == 200                # makes the folder
    folder = _parts_folder(app)
    lock = os.path.join(folder, slot + ".lock")
    open(lock, "wb").close()
    r = _piece(client, 2, "d" * 32, 0, MP4)
    assert r.status_code == 400 and "Reihenfolge" in r.json["error"]
    old = time.time() - 120
    os.utime(lock, (old, old))
    assert _piece(client, 2, "d" * 32, 0, MP4).status_code == 200
    assert not os.path.exists(lock)


def test_d1_old_pieces_are_swept(app, client, survey):
    switch_on(app)
    fill_consent(client)
    add(client, "media")
    assert _piece(client, 2, "e" * 32, 0, MP4).status_code == 200
    folder = _parts_folder(app)
    stale = os.path.join(folder, "99-" + "0" * 16 + "." + "f" * 32)
    with open(stale, "wb") as f:
        f.write(b"x" * 100)
    old = time.time() - media.PART_SECONDS - 10
    os.utime(stale, (old, old))
    with app.app_context():
        assert media.sweep_parts() == 1
    left = os.listdir(folder)
    assert len(left) == 1 and left[0].endswith("." + "e" * 32)                    # the fresh one stays


def test_d1_the_housekeeping_sweeps_the_pieces(app, monkeypatch):
    from app import lifecycle

    calls = []
    monkeypatch.setattr(media, "sweep_parts", lambda: calls.append(1) or 0)
    monkeypatch.setattr(lifecycle, "_last_rare", None)
    with app.app_context():
        lifecycle.close_due_surveys()
    assert calls == [1]


def _with_video(app, client, size):
    switch_on(app)
    fill_consent(client)
    add(client, "media")
    data = MP4 + b"\x01" * (size - len(MP4))
    assert _upload(client, data).status_code == 200
    return data


def test_d1_copying_a_question_keeps_the_room_of_the_target(app, client, survey):
    app.config["MEDIA_SURVEY_MAX_MB"] = 1
    _with_video(app, client, 600 * 1024)
    client.post("/surveys/new", data={"title": "Ziel", "languages": ["de"], "default_language": "de"})
    with app.app_context():
        code = db.session.scalar(db.select(Survey).filter_by(title="Ziel")).code
    client.post(E + "/items/2/copy-to", data={"target": code})                    # fits: 0.6 of 1 MB
    page = client.post(E + "/items/2/copy-to", data={"target": code}, follow_redirects=True).data.decode()
    assert "nicht kopiert" in page and "1 MB" in page
    with app.app_context():
        target = db.session.scalar(db.select(Survey).filter_by(code=code))
        first, second = [i for b in target.blocks for i in b.items if i.type == "media"]
        assert len(first.assets) == 1 and first.config["files"][""]["bytes"] == 600 * 1024
        assert second.assets == [] and "" not in (second.config.get("files") or {})
        assert media.media_bytes(target) == 600 * 1024


def test_d1_duplicating_keeps_the_room(app, client, survey):
    _with_video(app, client, 600 * 1024)
    app.config["MEDIA_SURVEY_MAX_MB"] = 0                                         # lowered after the upload
    page = client.post("/surveys/1/duplicate", data={"title": "Kopie", "with_people": "1"}, follow_redirects=True).data.decode()
    assert "nicht kopiert" in page
    with app.app_context():
        copy = db.session.scalar(db.select(Survey).filter_by(title="Kopie"))
        item = next(i for b in copy.blocks for i in b.items if i.type == "media")
        assert item.assets == [] and media.media_bytes(copy) == 0
    app.config["MEDIA_SURVEY_MAX_MB"] = 1
    client.post("/surveys/1/duplicate", data={"title": "Kopie 2", "with_people": "1"})
    with app.app_context():
        copy = db.session.scalar(db.select(Survey).filter_by(title="Kopie 2"))
        assert media.media_bytes(copy) == 600 * 1024


# --- D2 ------------------------------------------------------------------------------------------------------------


def _docx(body_xml):
    """A .docx of its main part only: nothing else of the file is read."""
    xml = ('<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
           'wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
           "<w:body>" + body_xml + "</w:body></w:document>").encode()
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", xml)
    return out.getvalue()


@pytest.fixture
def no_python_docx(monkeypatch):
    import docx

    monkeypatch.setattr(docx, "Document", lambda *a, **k: pytest.fail("python-docx must not read the upload"))


def test_d2_read_as_python_docx_did(app, no_python_docx):
    """Paragraphs, breaks, tabs, links, text boxes left out; a table with a head, a cell spanning two columns, a
    cell merged with the one above and a table inside a cell (left out, as python-docx did)."""
    from app import interview_transcribe

    def r(*inner):
        return "<w:r>" + "".join(inner) + "</w:r>"

    def t(text):
        return f"<w:t xml:space=\"preserve\">{text}</w:t>"

    def tc(text, pr=""):
        return f"<w:tc>{('<w:tcPr>' + pr + '</w:tcPr>') if pr else ''}<w:p>{r(t(text)) if text else ''}</w:p></w:tc>"

    body = ("<w:p>" + r(t("I: Wie geht es?")) + "</w:p>"
            + "<w:p>" + r(t("B: Gut"), "<w:br/>", t("neue Zeile"), "<w:tab/>", t("Tab"), '<w:br w:type="page"/>')
            + '<w:hyperlink r:id="x">' + r(t(" Link")) + "</w:hyperlink>" + r("<w:noBreakHyphen/>")
            + r('<w:drawing><w:txbxContent><w:p>' + r(t("Textfeld")) + "</w:p></w:txbxContent></w:drawing>") + "</w:p>"
            + "<w:p>" + r(t("   ")) + "</w:p>"
            + "<w:tbl><w:tr>" + tc("Sprecher") + tc("Zeit") + tc("Text") + "</w:tr>"
            + "<w:tr>" + tc("I", '<w:vMerge w:val="restart"/>') + tc("00:00:01") + tc("Wie kam es dazu?") + "</w:tr>"
            + "<w:tr>" + tc("", "<w:vMerge/>") + tc("00:00:04") + tc("Weiter.") + "</w:tr>"
            + "<w:tr>" + tc("B") + tc("Nur Text", '<w:gridSpan w:val="2"/>') + "</w:tr>"
            + "<w:tr>" + tc("B") + "<w:tc><w:p>" + r(t("Außen")) + "</w:p><w:tbl><w:tr>" + tc("innen") + "</w:tr></w:tbl></w:tc>"
            + tc("") + "</w:tr></w:tbl>"
            + "<w:p>" + r(t("Ende")) + "</w:p>")
    with app.app_context():
        text = interview_transcribe.read_upload("t.docx", _docx(body))
    assert text == ("I: Wie geht es?\n\nB: Gut\nneue Zeile\tTab Link-\n\nI: Wie kam es dazu? #00:00:01-0#\n\n"
                    "I: Weiter. #00:00:04-0#\n\nB: Nur Text\n\nB: Außen\n\nEnde")


def test_d2_a_dense_file_is_refused_or_cut_without_python_docx(app, monkeypatch, no_python_docx):
    """The repro of the audit (dateien/d_docx_dense.py): 1.9 to 3.3 MB of empty paragraphs or tiny cells took 1 GB of
    memory and up to 90 s. Now word/document.xml is read as a stream: past DOCX_MAX_ELEMENTS elements it is
    refused, past MAX_TEXT characters reading stops, past DOCX_MAX_DOCUMENT bytes it is refused from the header."""
    from app import interview_transcribe

    with app.app_context():
        monkeypatch.setattr(interview_transcribe, "DOCX_MAX_ELEMENTS", 5000)
        with pytest.raises(ValueError, match="zu viele Elemente"):
            interview_transcribe.read_upload("t.docx", _docx("<w:p/>" * 6000))
        with pytest.raises(ValueError, match="zu viele Elemente"):
            interview_transcribe.read_upload("t.docx", _docx("<w:tbl><w:tr>" + "<w:tc/>" * 6000 + "</w:tr></w:tbl>"))
        monkeypatch.setattr(interview_transcribe, "DOCX_MAX_ELEMENTS", 500_000)
        monkeypatch.setattr(interview_transcribe, "MAX_TEXT", 1000)
        text = interview_transcribe.read_upload("t.docx", _docx("<w:p><w:r><w:t>Satz eins zwei drei.</w:t></w:r></w:p>" * 4000))
        assert 1000 <= len(text) < 1100                                          # stopped, not read to the end
        monkeypatch.setattr(interview_transcribe, "DOCX_MAX_DOCUMENT", 64 * 1024)
        with pytest.raises(ValueError, match="entpackt zu groß"):
            interview_transcribe.read_upload("t.docx", _docx("".join(f"<w:p><w:r><w:t>{n}</w:t></w:r></w:p>" for n in range(5000))))
        with pytest.raises(ValueError, match="ließ sich nicht lesen"):
            interview_transcribe.read_upload("t.docx", _docx("<w:p><w:r><w:t>offen</w:r></w:p>"))


def test_d2_entities_are_not_expanded(app, no_python_docx):
    from app import interview_transcribe

    xml = ('<?xml version="1.0"?><!DOCTYPE d [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
           '<w:p><w:r><w:t>x&b;y</w:t></w:r></w:p></w:body></w:document>').encode()
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("word/document.xml", xml)
    with app.app_context():
        try:
            text = interview_transcribe.read_upload("t.docx", out.getvalue())
        except ValueError:
            return                                                                 # refused: fine as well
    assert "aaaa" not in text


# --- D3 ------------------------------------------------------------------------------------------------------------


def test_d3_the_zips_are_temporary_files(app, survey):
    from app import comment_shots
    from app.items import voice

    with app.app_context():
        s = db.session.get(Survey, 1)
        f = voice.zip_file(s, [])
        assert not isinstance(f, (bytes, io.BytesIO)) and zipfile.ZipFile(f).namelist() == []
        f = comment_shots.zip_file(s, "a;b\n", [])
        assert not isinstance(f, (bytes, io.BytesIO)) and zipfile.ZipFile(f).read("comments.csv") == b"a;b\n"
