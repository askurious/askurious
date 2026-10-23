"""Video / audio stimulus with a playback log (experimental "media", app/items/stimulus.py): upload in pieces,
serving in ranges, the rules for going on, the figures worked out on the server, export and long format."""
import io
import json

import pytest

from app import export
from app.extensions import db
from app.items.stimulus import CHUNK_BYTES, figures, read_log, sniff
from app.models import Item, Response, Survey, User
from tests.conftest import CONSENT_OK, add, fill_consent, fill_questions

E = "/surveys/1/editor"
MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 200
UPLOAD = "0123456789abcdef0123456789abcdef"


def switch_on(app):
    with app.app_context():
        for u in db.session.scalars(db.select(User)):
            u.experiments = ["media"]
        db.session.commit()


def _upload(client, data=MP4, seconds="60", kind="video", upload=UPLOAD):
    pieces = [data[i:i + CHUNK_BYTES] for i in range(0, len(data), CHUNK_BYTES)] or [b""]
    for n, piece in enumerate(pieces):
        form = {"upload": upload, "index": str(n), "file": (io.BytesIO(piece), "spot.mp4")}
        if n == len(pieces) - 1:
            form.update(last="1", seconds=seconds, kind=kind)
        r = client.post(E + "/items/2/media", data=form, content_type="multipart/form-data")
    return r


def _media(app, client, **settings):
    switch_on(app)
    fill_consent(client)
    add(client, "media")                                                       # item 2
    client.post(E + "/items/2", data={"code": "spot", "question": "Bitte ansehen", "seek": "back",
                                      "advance": "any", "share": "90", **settings})


def _log(*rows):
    return json.dumps([list(r) for r in rows])


def test_for_everyone_without_a_tick(app, client, survey):
    """Experimental until 2026-10-16, since then for everyone (Ralf)."""
    fill_consent(client)
    add(client, "media")
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.type == "media" and item.config["seek"] == "back" and item.config["advance"] == "any"


def test_sniff_reads_the_first_bytes():
    assert sniff(MP4[:16]) == "video/mp4"
    assert sniff(b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 4) == "audio/mp4"
    assert sniff(b"\x1a\x45\xdf\xa3" + b"\x00" * 12) == "video/webm"
    assert sniff(b"ID3\x04" + b"\x00" * 12) == "audio/mpeg"
    assert sniff(b"RIFF\x00\x00\x00\x00WAVEfmt ") == "audio/wav"
    assert sniff(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8) is None


def test_upload_in_pieces_and_serving_in_ranges(app, client, survey):
    _media(app, client)
    data = MP4 + b"\x01" * (CHUNK_BYTES + 10)                                   # two pieces
    r = _upload(client, data, seconds="61.5")
    assert r.status_code == 200 and r.json["ok"] and r.json["bytes"] == len(data)
    with app.app_context():
        item = db.session.get(Item, 2)
        info = item.kind.info(item)
        assert info["kind"] == "video" and info["mime"] == "video/mp4" and info["seconds"] == 61.5 and info["name"] == "spot.mp4"
        name, uid = item.assets[0].filename, item.survey.uid
        assert name.endswith(".mp4") and item.kind.validate(item) == []
    r = client.get(f"/media/{uid}/{name}", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and r.headers["Content-Type"] == "video/mp4" and len(r.data) == 100
    r.close()  # the file stays open until then (Windows cannot replace it)
    # a second file takes the place of the first
    _upload(client, MP4, upload="f" * 32)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert len(item.assets) == 1 and item.assets[0].filename != name
    assert client.get(f"/media/{uid}/{name}").status_code == 404


def test_upload_refused(app, client, survey):
    _media(app, client)
    r = _upload(client, b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    assert r.status_code == 400 and "MP4" in r.json["error"]
    r = client.post(E + "/items/2/media", data={"upload": UPLOAD, "index": "1", "file": (io.BytesIO(b"x"), "a.mp4")},
                    content_type="multipart/form-data")
    assert r.status_code == 400                                                # a piece out of order
    app.config["MEDIA_MAX_MB"] = 0
    r = _upload(client)
    assert r.status_code == 400 and "MB" in r.json["error"]


def test_figures_from_the_log():
    log, cut = read_log(_log((100, "play", 0), (5100, "seg", 0, 5, 0), (5100, "seek", 5, 2), (5200, "play", 2),
                             (9000, "hide"), (9000, "seg", 2, 6, 0), (12000, "seg", 6, 9, 1), (12000, "pause", 9),
                             (12100, "mute", 1)), 10)
    f = figures({"log": log, "dur": 10})
    assert cut == 0
    assert f["started"] == 1 and f["seen"] == 0.9 and f["complete"] == 0      # 0-9 once, 2-5 twice
    assert f["seconds"] == 12.0 and f["hidden"] == 3.0 and f["muted"] == 1
    assert f["plays"] == 2 and f["pauses"] == 1 and f["seeks"] == 1
    assert figures(None) is None                                               # no log: empty, not 0
    assert figures({"log": [], "dur": 10})["started"] == 0


@pytest.mark.parametrize("raw", ['{"a": 1}', _log((10, "jump", 1)), _log((10, "play", 99)), _log((20, "play", 1), (10, "pause", 2)),
                                 _log((10, "seg", 5, 2, 0)), _log((10, "play", "1"))])
def test_a_log_no_player_writes_is_refused(raw):
    with pytest.raises((ValueError, TypeError)):
        read_log(raw, 10)


def _start(app, client):
    fill_questions(app)
    r = client.post("/surveys/1/start", data={})
    with app.app_context():
        survey = db.session.get(Survey, 1)
        assert survey.status == "running", r.data[:2000]
        return f"/s/{survey.code}"


def _participant(app, url):
    p = app.test_client()
    p.get(url)
    p.post(url, data=CONSENT_OK)
    return p


def _answer(app):
    with app.app_context():
        r = db.session.scalars(db.select(Response).filter_by(is_preview=False).order_by(Response.id.desc())).first()
        return r.answers.get("2")


def test_page_shows_player_and_stores_the_log(app, client, survey):
    _media(app, client, captions="00:00.000 --> 00:02.000\nHallo", transcript="Ein Sprecher sagt Hallo.")
    _upload(client, seconds="10")
    url = _start(app, client)
    p = _participant(app, url)
    html = p.get(url).data.decode()
    assert "data-media " in html and 'data-seek="back"' in html and "<video" in html and 'name="i2"' in html
    assert "WEBVTT" in html and "Ein Sprecher sagt Hallo." in html and "Bitte schalten Sie den Ton ein." in html
    # not played to the end: a soft hint once, then on
    log = _log((100, "play", 0), (4100, "seg", 0, 4, 0), (4100, "pause", 4))
    r = p.post(url, data={"nav": "next", "i2": log})
    assert "noch nicht zu Ende" in r.data.decode()
    p.post(url, data={"nav": "next", "i2": log})
    answer = _answer(app)
    assert answer["dur"] == 10 and answer["log"][0] == [100, "play", 0.0]


def test_advance_end_holds_the_page(app, client, survey):
    _media(app, client, advance="end")
    _upload(client, seconds="10")
    url = _start(app, client)
    p = _participant(app, url)
    p.get(url)
    r = p.post(url, data={"nav": "next", "i2": _log((100, "play", 0), (4100, "seg", 0, 4, 0), (4100, "pause", 4))})
    assert "bis zum Ende" in r.data.decode() and _answer(app) is None
    # a file that does not play keeps nobody
    p.post(url, data={"nav": "next", "i2": _log((100, "error", 4))})
    assert _answer(app)["log"] == [[100, "error", 4]]


def test_export_columns_and_long_format(app, client, survey):
    _media(app, client)
    _upload(client, seconds="10")
    url = _start(app, client)
    p = _participant(app, url)
    p.get(url)
    p.post(url, data={"nav": "next", "i2": _log((100, "play", 0), (10100, "seg", 0, 10, 0), (10100, "end", 10))})
    with app.app_context():
        survey = db.session.get(Survey, 1)
        item = db.session.get(Item, 2)
        response = db.session.scalars(db.select(Response).filter_by(is_preview=False)).first()
        values = item.kind.export_values(item, response.answers["2"])
        assert values["spot_started"] == 1 and values["spot_seen"] == 1.0 and values["spot_complete"] == 1
        assert [c for c, _ in item.kind.export_columns(item)][:3] == ["spot_started", "spot_seen", "spot_complete"]
        rows = list(item.kind.long_rows(item, response))
        assert rows[0] == [1, 100, "play", 0.0, "", ""] and rows[1][:3] == [2, 10100, "seg"]
        text = export.long_csv(survey, item)
        assert "event" in text and "seg" in text
        # without a log (no JavaScript) the figures stay empty
        assert item.kind.export_values(item, None)["spot_started"] is None


def test_privacy_text_names_the_log(app, client, survey):
    _media(app, client)
    with app.app_context():
        consent = db.session.get(Item, 1)
        facts = consent.kind.facts(consent, "de")
        assert facts["media_playback"]
        assert "abgespielt" in consent.kind.template_body(consent, "de")


# --- stage 2: results, quality control, a file per loop value -------------------------------------------------------


def test_results_curve(app, client, survey):
    from app import results

    _media(app, client)
    _upload(client, seconds="10")
    url = _start(app, client)
    for log in (_log((100, "play", 0), (10100, "seg", 0, 10, 0), (10100, "end", 10)),
                _log((100, "play", 0), (5100, "seg", 0, 5, 0), (5100, "pause", 5)),
                "[]"):
        p = _participant(app, url)
        p.get(url)
        p.post(url, data={"nav": "next", "i2": log})
        p.post(url, data={"nav": "next", "i2": log})  # past the soft hint
    with app.app_context():
        data = results.compute(db.session.get(Survey, 1), include_incomplete=True)
        m = next(e for e in data["items"] if e["item"].id == 2)["media"]
        assert m["persons"] == 3 and round(m["started"], 3) == round(2 / 3, 3) and round(m["complete"], 3) == round(1 / 3, 3)
        line = m["lines"][0]
        assert line["n"] == 3 and round(line["mid"], 3) == round(2 / 3, 3) and round(line["end"], 3) == round(1 / 3, 3)
    html = client.get("/surveys/1/results?drop_speeders=0&incomplete=1").data.decode()
    assert "media-results" in html or "Protokolle" in html


def test_quality_values():
    from app.quality import stimulus_values

    played = {"log": [[0, "play", 0], [9000, "seg", 0, 9, 0], [9000, "pause", 9]], "dur": 10}
    hidden = {"log": [[0, "play", 0], [4000, "seg", 0, 4, 1], [10000, "seg", 4, 10, 0], [10000, "end", 10]], "dur": 10}
    assert stimulus_values({"2": played, "5": hidden}, {2, 5}) == {"qc_stimulus": 0.9, "qc_background": 0.211}
    assert stimulus_values({"2": None}, {2}) == {}
    assert stimulus_values({"2#r1": played}, {2})["qc_stimulus"] == 0.9


def test_quality_flags_are_opt_in(app, client, survey):
    from app import export, quality

    _media(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        ctx = quality.context(s, export._items(s))
        assert "stimulus" not in ctx["rules"]["flags"] and ctx["media"] == {2}
        assert [k for k, *_ in quality.available(ctx)][-2:] == ["stimulus", "background"]
        assert ("qc_stimulus", quality.MEDIA_COLUMNS[0][1]) in quality.columns(export._items(s))


def test_a_file_per_loop_value(app, client, survey):
    from tests.editor.test_loops import setup

    switch_on(app)
    setup(client)                                                   # loop robot x setting over block 2, item 2
    add(client, "media")                                            # item 3, in the loop
    client.post(E + "/items/3", data={"code": "spot", "seek": "back", "advance": "any", "share": "90", "by": "robot"})
    with app.app_context():
        item = db.session.get(Item, 3)
        keys = item.kind.required_keys(item)
        assert [v for _, v in keys] == ["Pflegeroboter", "Serviceroboter"]
        assert "noch ohne Datei: Pflegeroboter, Serviceroboter" in item.kind.validate(item)
    editor = client.get(E + "/items/3").data.decode()
    assert editor.count("data-media-upload") == 2 and 'option value="robot" selected' in editor
    for n, (key, _) in enumerate(keys):
        form = {"upload": f"{n:032x}", "index": "0", "key": key, "last": "1", "seconds": str(10 + n), "kind": "video",
                "file": (io.BytesIO(MP4), "spot.mp4")}
        assert client.post(E + "/items/3/media", data=form, content_type="multipart/form-data").status_code == 200
    r = client.post(E + "/items/3/media", data={"upload": "e" * 32, "index": "0", "key": "nope", "last": "1",
                                                "file": (io.BytesIO(MP4), "x.mp4")}, content_type="multipart/form-data")
    assert r.status_code == 400
    with app.app_context():
        item = db.session.get(Item, 3)
        assert item.kind.validate(item) == [] and len(item.assets) == 2
        key = item.kind.key_for(item, {"robot": "Serviceroboter", "setting": "im Heim"})
        assert item.kind.info(item, key)["seconds"] == 11
        answer = item.kind.parse_answer(item, {"i3": _log((0, "play", 0)), "i3_key": key})
        assert answer["key"] == key and answer["dur"] == 11
        assert item.kind.parse_answer(item, {"i3": "[]", "i3_key": "forged"})["key"] == ""
