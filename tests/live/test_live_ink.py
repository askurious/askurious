"""Pen notes on presentation slides of a live vote (app/live/ink.py, live_ink.js; concept
stiftnotizen-konzept-2026-10-23.md, Ralf 2026-10-23: all as recommended): strokes saved per run and slide with the
pen's pressure, shown on the screen by revision, eraser and "Folie leeren", the limits, new and merged runs, the PDF."""
import io
import json

from app.extensions import db
from app.models import LiveInk, Survey
from tests.live.test_live import add, make, steer
from tests.live.test_live_slides import _slides, _upload


def _stroke(sid="a1", points=((0.1, 0.2, 0.3), (0.5, 0.6, 0.9)), **more):
    return {"id": sid, "c": "#d32f2f", "w": 0.003, "p": [list(p) for p in points], **more}


def _save(client, code, item, ops=None, laser=None, **more):
    data = {"item": str(item), "ops": json.dumps(ops or []), **more}
    if laser is not False:
        data["laser"] = json.dumps(laser)
    return client.post(f"/presenter/{code}/ink", data=data)


def _ink(client, code, item, **args):
    query = "&".join(f"{k}={v}" for k, v in args.items())
    return client.get(f"/presenter/{code}/ink.json?item={item}" + ("&" + query if query else "")).json


def _deck(app, client, pages=2):
    code, sid, block = make(app, client)
    for n in range(1, pages + 1):
        assert _upload(client, code, n).json["ok"]
    return code, sid, block, [i for i, *_ in _slides(app, sid)]


def test_strokes_are_saved_with_pressure_and_shown_by_revision(app, client):
    code, sid, block, (first, second) = _deck(app, client)
    steer(client, code, action="start")
    assert client.get(f"/presenter/{code}/result.json").json["current"]["ink"] == {"rev": 0, "active": False, "screen": True}
    r = _save(client, code, first, [{"put": _stroke(points=((0.1, 0.2, 0.3), (1.7, -1, 4)))}], laser=False)
    assert r.json == {"ok": True, "rev": 1}
    got = _ink(client, code, first)
    assert got["rev"] == 1 and got["strokes"][0]["p"] == [[0.1, 0.2, 0.3], [1.0, 0.0, 1.0]]      # clamped to the slide
    assert got["strokes"][0]["m"] == 0 and got["strokes"][0]["c"] == "#d32f2f"
    assert _ink(client, code, first, rev=1) == {"rev": 1, "active": True, "laser": None, "same": True}
    state = client.get(f"/presenter/{code}/result.json").json["current"]["ink"]
    assert state["rev"] == 1 and state["active"] is True                                      # the screen asks faster
    # a stroke drawn further keeps its id and replaces itself; a broken one is left out
    _save(client, code, first, [{"put": _stroke(points=((0.1, 0.2, 0.5),) * 3)}, {"put": {"id": "x y", "p": [[0, 0]]}},
                                {"put": _stroke("b2", c="red")}])
    assert [s["id"] for s in _ink(client, code, first)["strokes"]] == ["a1"]
    assert _ink(client, code, second)["strokes"] == []                                        # per slide


def test_eraser_clear_and_undo(app, client):
    code, sid, block, (first, _) = _deck(app, client)
    steer(client, code, action="start")
    _save(client, code, first, [{"put": _stroke("a")}, {"put": _stroke("b")}, {"put": _stroke("c", m=1)}])
    _save(client, code, first, [{"del": ["b"]}])                                              # the eraser
    assert [s["id"] for s in _ink(client, code, first)["strokes"]] == ["a", "c"]
    _save(client, code, first, [{"clear": 1}])                                                # "Folie leeren"
    assert _ink(client, code, first)["strokes"] == []
    _save(client, code, first, [{"put": _stroke("a")}, {"put": _stroke("c", m=1)}])           # Undo puts them back
    assert [s["m"] for s in _ink(client, code, first)["strokes"]] == [0, 1]


def test_limits_and_rights(app, client, monkeypatch):
    from app.live import ink

    code, sid, block, (first, _) = _deck(app, client)
    q = add(app, client, sid, block, "rating", question="Wie war es?")
    steer(client, code, action="start")
    monkeypatch.setattr(ink, "MAX_STROKES", 2)
    r = _save(client, code, first, [{"put": _stroke(str(n))} for n in range(3)])
    assert r.status_code == 413 and "kein Platz" in r.json["error"]
    assert _ink(client, code, first)["strokes"] == []                                         # nothing saved
    assert _save(client, code, q, [{"put": _stroke()}]).status_code == 404                    # only presentation slides
    assert client.get(f"/presenter/{code}/ink.json?item={q}").status_code == 404
    other = app.test_client()
    assert other.post(f"/presenter/{code}/ink", data={"item": str(first)}).status_code in (302, 401)


def test_the_laser_dot_shows_briefly_and_is_not_a_stroke(app, client, monkeypatch):
    from app.live import ink

    code, sid, block, (first, _) = _deck(app, client)
    steer(client, code, action="start")
    _save(client, code, first, laser=[0.4, 0.5])
    got = _ink(client, code, first)
    assert got["laser"] == [0.4, 0.5] and got["strokes"] == [] and got["rev"] == 0
    monkeypatch.setattr(ink, "LASER_SECONDS", 0)                                              # as two seconds later
    assert _ink(client, code, first)["laser"] is None
    _save(client, code, first, laser=None)
    with app.app_context():
        assert db.session.scalar(db.select(LiveInk.laser)) is None


def test_the_screen_can_be_kept_free(app, client):
    code, sid, block, (first, _) = _deck(app, client)
    steer(client, code, action="start")
    steer(client, code, action="ink_off")
    assert client.get(f"/presenter/{code}/result.json").json["current"]["ink"]["screen"] is False
    steer(client, code, action="ink_on")
    assert client.get(f"/presenter/{code}/result.json").json["current"]["ink"]["screen"] is True


def test_the_presenter_view_has_the_pen_the_projector_not(app, client):
    code, *_ = _deck(app, client, pages=1)
    speaker = client.get(f"/presenter/{code}/speaker").data.decode()
    assert "data-ink-tools" in speaker and "live_ink.js" in speaker and 'data-ink-tool="eraser"' in speaker
    assert "data-ink-open" in speaker and 'aria-controls="live-ink-pop"' in speaker                  # a popup at a button
    projector = client.get(f"/presenter/{code}/screen").data.decode()
    assert "data-ink-tools" not in projector and "live_ink.js" in projector                   # it shows the strokes


def test_notes_belong_to_their_run(app, client):
    code, sid, block, (first, _) = _deck(app, client)
    steer(client, code, action="start")
    _save(client, code, first, [{"put": _stroke("old")}])
    with app.app_context():
        old = db.session.get(Survey, sid).live["run"]["id"]
    steer(client, code, action="new_run")
    assert _ink(client, code, first)["strokes"] == []                                         # a new run starts clean
    _save(client, code, first, [{"put": _stroke("new")}])
    assert [s["id"] for s in _ink(client, code, first, durchgang=old)["strokes"]] == ["old"]
    client.post(f"/presenter/{code}/control", data={"action": "run_merge", "run": str(old), "into": str(_current_run(app, sid))})
    assert [s["id"] for s in _ink(client, code, first)["strokes"]] == ["new", "old"]           # merged: after the target's
    steer(client, code, action="restart")                                                     # "Alles zurück"
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(LiveInk.id))) == 0


def _current_run(app, sid):
    with app.app_context():
        return db.session.get(Survey, sid).live["run"]["id"]


def test_the_pdf_has_every_slide_with_its_notes(app, client):
    from pypdf import PdfReader

    code, sid, block, (first, second) = _deck(app, client)
    add(app, client, sid, block, "rating", question="Wie war es?")
    steer(client, code, action="start")
    _save(client, code, first, [{"put": _stroke("a")}, {"put": _stroke("m", m=1, c="#ffd400")}, {"put": _stroke("dot", points=((0.5, 0.5, 1),))}])
    r = client.get(f"/presenter/{code}/notes.pdf")
    assert r.status_code == 200 and r.mimetype == "application/pdf"
    assert "folien-notizen-" in r.headers["Content-Disposition"]
    pages = PdfReader(io.BytesIO(r.data)).pages
    assert len(pages) == 2                                                                    # the presentation slides only
    assert float(pages[0].mediabox.width) == 842.0 and round(float(pages[0].mediabox.height)) == round(842 * 180 / 320)
    content = pages[0].get_contents().get_data()
    assert content.count(b" l") + content.count(b" l\n") >= 2 and b" Do" in content           # the picture and the lines
    assert client.get(f"/presenter/{code}/notes.pdf?durchgang=123").status_code == 404
    assert "Folien mit Notizen (PDF)" in client.get(f"/surveys/{sid}/data").data.decode()
    assert "Folien mit Notizen (PDF)" in client.get(f"/presenter/{code}/slides").data.decode()


def test_deleting_the_survey_takes_the_notes(app, client):
    from app import lifecycle

    code, sid, block, (first,) = _deck(app, client, pages=1)
    steer(client, code, action="start")
    _save(client, code, first, [{"put": _stroke()}])
    with app.app_context():
        lifecycle.delete(db.session.get(Survey, sid))
        db.session.commit()
        assert db.session.scalar(db.select(db.func.count(LiveInk.id))) == 0


def test_notes_of_any_run_are_shown_and_edited_on_all_slides(app, client):
    """E9: "Alle Folien" shows each presentation slide with the notes of the run chosen; who may edit gets "Notizen
    bearbeiten", and a change goes to that run, also an earlier one."""
    code, sid, block, (first, _) = _deck(app, client)
    steer(client, code, action="start")
    _save(client, code, first, [{"put": _stroke("old")}])
    old = _current_run(app, sid)
    steer(client, code, action="new_run")
    page = client.get(f"/presenter/{code}/slides?durchgang={old}").data.decode()
    assert "data-ink-edit" in page and f'data-ink-run="{old}"' in page and "live_ink.js" in page
    import html

    assert '"kind": "slide"' in html.unescape(page) and '"id": "old"' in html.unescape(page)   # the strokes of that run
    assert _save(client, code, first, [{"put": _stroke("later")}], durchgang=str(old)).json["ok"]
    assert [s["id"] for s in _ink(client, code, first, durchgang=old)["strokes"]] == ["old", "later"]
    assert _ink(client, code, first)["strokes"] == []                                         # the current run untouched
    assert client.post(f"/presenter/{code}/ink", data={"item": str(first), "durchgang": "999", "ops": "[]"}).status_code == 404
