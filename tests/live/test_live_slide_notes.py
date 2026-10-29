"""The audience's notes on presentation slides of a live vote (app/live/notes.py, live_notes.js; concept folien-notizen,
Ralf 2026-10-29: all as recommended): the option, the limit at the presenter's current slide, the PDF with portrait
and landscape pages, nothing stored, one PDF per minute."""
import io
import json

from app.extensions import db
from app.models import LiveInk, RateHit, Survey
from tests.live.test_live import add, make, steer
from tests.live.test_live_ink import _save, _stroke
from tests.live.test_live_slides import _slides, _upload


def _deck(app, client, pages=3, notes=True):
    code, sid, block = make(app, client)
    for n in range(1, pages + 1):
        assert _upload(client, code, n).json["ok"]
    with app.app_context():
        survey = db.session.get(Survey, sid)
        survey.live = {**survey.live, "slide_phone": True, "slide_notes": notes}
        db.session.commit()
    return code, sid, block, [i for i, *_ in _slides(app, sid)]


def _phone(app, code):
    p = app.test_client()
    p.get(f"/live/{code}")
    return p


def _pdf(p, code, notes=None, **ticks):
    data = {"notes": json.dumps(notes or {}), **{k: "1" for k in ("mine_ink", "mine_text", "presenter")}, **ticks}
    return p.post(f"/live/{code}/notizen.pdf", data=data)


def test_off_without_the_tick_or_without_slides_on_the_phones(app, client):
    code, sid, block, (first, *_) = _deck(app, client, notes=False)
    steer(client, code, action="start")
    p = _phone(app, code)
    assert "data-live-notes" not in p.get(f"/live/{code}").data.decode()
    assert p.get(f"/live/{code}/folie/{first}").status_code == 404
    assert p.get(f"/live/{code}/folie/{first}/striche").status_code == 404
    assert _pdf(p, code).status_code == 404
    with app.app_context():  # the tick counts only with "Präsentationsfolien auch auf den Handys zeigen" (E2)
        survey = db.session.get(Survey, sid)
        survey.live = {**survey.live, "slide_phone": False, "slide_notes": True}
        db.session.commit()
    assert _pdf(p, code).status_code == 404
    assert "upto" not in p.get(f"/live/{code}/state").json


def test_the_setting_is_saved_and_indented(app, client):
    code, sid, block, _ = _deck(app, client, notes=False)
    client.post(f"/presenter/{code}/settings", data={"part": "teilnahme", "slides_shown": "1", "slide_phone": "1", "slide_notes": "1"})
    with app.app_context():
        assert db.session.get(Survey, sid).live["slide_notes"] is True
    page = client.get(f"/presenter/{code}/settings").data.decode()
    assert 'class="check live-indent"><input type="checkbox" name="slide_notes" value="1" checked>' in page
    assert "Notizen und Herunterladen erlauben" in page


def test_the_phone_pages_back_but_never_beyond_the_current_slide(app, client):
    code, sid, block, (first, second, third) = _deck(app, client)
    p = _phone(app, code)
    assert p.get(f"/live/{code}/folie/{first}").status_code == 403                       # nothing shown yet
    steer(client, code, action="start")
    page = p.get(f"/live/{code}").data.decode()
    assert "data-live-notes" in page and "live_notes.js" in page and "ink_core.js" in page
    assert f'data-slides=\'[[{first}, 1], [{second}, 2], [{third}, 3]]\'' in page and 'data-upto="1"' in page
    assert p.get(f"/live/{code}/folie/{first}").status_code == 200
    assert p.get(f"/live/{code}/folie/{second}").status_code == 403
    steer(client, code, action="next")
    steer(client, code, action="next")
    assert p.get(f"/live/{code}/state").json["upto"] == 3
    assert p.get(f"/live/{code}/folie/{third}?klein=1").status_code == 200
    steer(client, code, action="prev")                                                   # back: the third is locked again
    assert p.get(f"/live/{code}/state").json["upto"] == 2
    assert p.get(f"/live/{code}/folie/{third}").status_code == 403
    assert p.get(f"/live/{code}/folie/{third}/striche").status_code == 403
    steer(client, code, action="end")                                                    # after the end: as far as shown (E8)
    page = p.get(f"/live/{code}").data.decode()
    assert p.get(f"/live/{code}/state").json["upto"] == 2 and "data-notes-download" in page
    assert p.get(f"/live/{code}/folie/{second}").status_code == 200
    assert p.get(f"/live/{code}/folie/{third}").status_code == 403


def test_the_presenters_strokes_show_on_the_phone(app, client):
    code, sid, block, (first, second, _) = _deck(app, client)
    steer(client, code, action="start")
    _save(client, code, first, [{"put": _stroke("a")}])
    p = _phone(app, code)
    got = p.get(f"/live/{code}/folie/{first}/striche").json
    assert got["rev"] == 1 and got["strokes"][0]["id"] == "a"
    assert p.get(f"/live/{code}/folie/{first}/striche?rev=1").json == {"rev": 1, "same": True}
    assert p.get(f"/live/{code}/state").json["ink"] == 1
    steer(client, code, action="ink_off")                                                # kept off the screen: off the phones
    assert p.get(f"/live/{code}/folie/{first}/striche").json["strokes"] == []


def test_the_pdf_portrait_with_a_typed_note_landscape_without(app, client):
    from pypdf import PdfReader

    code, sid, block, (first, second, third) = _deck(app, client)
    add(app, client, sid, block, "rating", question="Wie war es?")
    steer(client, code, action="start")
    _save(client, code, first, [{"put": _stroke("a")}])
    steer(client, code, action="next")
    p = _phone(app, code)
    notes = {str(first): {"t": "Wichtig: Prüfung " * 400, "s": [_stroke("m1"), {"id": "bad id", "p": []}]},
             str(second): {"t": "", "s": [_stroke("m2")]}, str(third): {"t": "nicht gezeigt", "s": []}}
    r = _pdf(p, code, notes)
    assert r.status_code == 200 and r.mimetype == "application/pdf" and "folien-notizen-" in r.headers["Content-Disposition"]
    pages = PdfReader(io.BytesIO(r.data)).pages
    sizes = [(round(float(pg.mediabox.width)), round(float(pg.mediabox.height))) for pg in pages]
    # the first slide with its long note: A4 portrait, the note going on to a second portrait page; the second
    # slide without a typed note: landscape in its own shape; the third is not reached and not in it (E3, E7)
    assert sizes[:2] == [(595, 842), (595, 842)] and sizes[2:] == [(842, round(842 * 180 / 320))]
    assert "Wichtig: Pr" in pages[0].extract_text() and "nicht gezeigt" not in "".join(pg.extract_text() for pg in pages)
    # without the typed notes every page is landscape
    r = _pdf(p, code, notes, mine_text="")
    assert r.status_code == 429                                                          # one PDF per minute and device (E10)
    assert "Minute" in r.json["error"]


def test_the_pdf_stores_nothing(app, client):
    code, sid, block, (first, *_) = _deck(app, client)
    steer(client, code, action="start")
    with app.app_context():
        before = {name: db.session.scalar(db.select(db.func.count()).select_from(t)) for name, t in db.metadata.tables.items()
                  if name not in ("rate_hit", "rate_count", "browser_receipt")}
    p = _phone(app, code)
    secret = "Meine geheime Notiz 4711"
    r = _pdf(p, code, {str(first): {"t": secret, "s": [_stroke("x")]}}, mine_text="1")
    assert r.status_code == 200
    with app.app_context():
        after = {name: db.session.scalar(db.select(db.func.count()).select_from(t)) for name, t in db.metadata.tables.items()
                 if name not in ("rate_hit", "rate_count", "browser_receipt")}
        assert after == before                                                            # no row of the notes anywhere (E1)
        assert db.session.scalar(db.select(db.func.count(LiveInk.id))) == 0
        assert db.session.scalar(db.select(db.func.count(RateHit.id))) >= 1               # only the count of the limit
        assert secret not in json.dumps(db.session.get(Survey, sid).live)


def test_landscape_without_typed_notes_and_bad_input(app, client):
    from pypdf import PdfReader

    code, sid, block, (first, *_) = _deck(app, client, pages=1)
    steer(client, code, action="start")
    p = _phone(app, code)
    assert p.post(f"/live/{code}/notizen.pdf", data={"notes": "[1, 2]"}).status_code == 400
    assert p.post(f"/live/{code}/notizen.pdf", data={"notes": "x" * 6_000_001}).status_code == 413
    r = _pdf(p, code, {str(first): {"t": "nur getippt", "s": []}}, mine_text="")
    pg = PdfReader(io.BytesIO(r.data)).pages[0]
    assert float(pg.mediabox.width) > float(pg.mediabox.height) and "nur getippt" not in pg.extract_text()


def test_the_phone_keeps_the_slide_it_was_drawn_with():
    """Found with these notes: live.js replaced a presentation slide drawn with the phone's page by "Gleich geht es
    los …" at once (a slide key counted as waiting), so "Präsentationsfolien auch auf den Handys zeigen" showed no slide."""
    from pathlib import Path

    js = (Path(__file__).resolve().parents[2] / "app" / "static" / "live.js").read_text(encoding="utf-8")
    assert 'else if (shown && shown.indexOf("slide-") !== 0) {' in js


def test_the_participants_privacy_page_names_the_notes(app, client):
    code, sid, block, _ = _deck(app, client)
    with app.app_context():
        key = db.session.get(Survey, sid).public_key
    page = app.test_client().get(f"/s/{key}/privacy").data.decode()
    assert "bleiben nur im Speicher Ihres Browsers" in page
