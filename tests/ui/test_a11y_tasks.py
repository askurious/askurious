"""Accessibility of the participant pages, part 2 (concept "Barrierefreiheit", D2, D4, D5, D7): marking an image with
the keyboard, "Mehr Zeit" on pages with a time limit, the IAT's "Diese Aufgabe kann ich nicht bearbeiten", and what
timed tasks and pages that move on by themselves say and where they put the focus."""
from tests.static_sources import participant_js

import csv
import io
import os

from app.extensions import db
from app.models import Block, Item, Response
from tests.conftest import CONSENT_OK
from tests.items.test_iat import _iat, _start
from tests.items.test_image_mark import setup as mark_setup
from tests.analysis.test_pagetime import response, setup as time_setup, start, wait

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))


def _js():
    return participant_js()


def _rows(client):
    return list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))


# --- D5: IAT ---------------------------------------------------------------------------------------------------------

def test_iat_offers_a_way_out_before_the_start(app, client, survey):
    _iat(app, client)
    url = _start(app, client)
    p = app.test_client()
    p.get(url)
    p.post(url, data=CONSENT_OK)
    html = p.get(url).data.decode()
    assert "verlangt schnelle Tastendrücke" in html and "data-iat-cannot" in html
    assert "Diese Aufgabe kann ich nicht bearbeiten" in html
    assert 'data-iat-status' in html and 'role="status"' in html                     # an error said in words (D7)


def test_iat_cannot_is_a_missing_value_of_its_own(app, client, survey):
    from app.items.iat import CANNOT, CANNOT_CODE

    _iat(app, client)
    with app.app_context():
        item = db.session.get(Item, 2)
        item.answer_mode = "required"
        db.session.commit()
    url = _start(app, client)
    p = app.test_client()
    p.get(url)
    p.post(url, data=CONSENT_OK)
    assert p.post(url, data={"nav": "next", "i2": CANNOT}).status_code == 302          # a required IAT lets the person on
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(is_preview=False))
        item = db.session.get(Item, 2)
        assert r.answers["2"] == CANNOT and item.kind.scores(CANNOT) == {} and not item.kind.is_missing(item, CANNOT)
        code = item.code
        assert item.kind.missing_codes(item, f"{code}_d") == (CANNOT_CODE,)
        # the results and the method sentence leave the person out of n and say so
        table = [{f"{code}_valid": CANNOT_CODE}, {f"{code}_valid": 1}, {f"{code}_valid": 0}]
        assert item.kind.results(item, table) == {"n": 2, "valid": 1, "cannot": 1}
        assert "Weitere 1 gaben vor dem Start an" in item.kind.method_text(item, [CANNOT], "de")
        assert "said before the start" in item.kind.method_text(item, [CANNOT], "en")
    row = _rows(client)[0]
    assert all(row[f"{code}_{k}"] == str(CANNOT_CODE) for k in ("d", "d1", "d2", "valid", "order", "err", "fast"))
    codebook = client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    assert f"{CANNOT_CODE} = nicht bearbeitbar" in codebook and "Diese Aufgabe kann ich nicht bearbeiten" in codebook
    assert client.get("/surveys/1/results").status_code == 200


def test_iat_dummy_data_still_run_the_task(app, client, survey):
    _iat(app, client)
    client.post("/surveys/1/dummy", data={"n": "2"})
    with app.app_context():
        done = db.session.scalars(db.select(Response).filter_by(source="dummy")).all()
        assert done and all(isinstance(r.answers.get("2"), dict) for r in done)


# --- D4: time limit --------------------------------------------------------------------------------------------------

def test_more_time_extends_the_limit_up_to_ten_times_and_is_exported(app, client, survey):
    """WCAG 2.2.1: "Mehr Zeit" at least ten times, and the limit said beforehand (UI/UX audit 2026-10-16, 80)."""
    time_setup(client, required=True, max_seconds=30)
    p, url = start(app, client)
    html = p.get(url).data.decode()
    assert 'data-more="30"' in html and "data-hold-more" in html and "Mehr Zeit" in html and 'data-more-limit="10"' in html
    assert "Für diese Seite haben Sie 30 Sekunden Zeit. Kurz vor Ablauf können Sie mit „Mehr Zeit“ verlängern." in html
    # "Mehr Zeit" sent with the page (an error keeps it on screen): the clock now runs 60 s, also after a reload
    again = p.post(url, data={"pos": "r1", "nav": "next", "time_more": "1", "i2": ""}).data.decode()
    assert "has-error" in again and 'name="time_more" value="1"' in again and "data-hold-more" in again  # offered again
    data = response(app)[3]
    assert data["_hold"]["more"] == 1 and len(data["_more"]) == 1
    wait(app, 30)
    assert p.post(url, data={"pos": "r1", "nav": "next", "time_up": "1", "time_more": "1", "i2": ""}).status_code == 200
    wait(app, 30)
    assert p.post(url, data={"pos": "r1", "nav": "next", "time_up": "1", "time_more": "1", "i2": ""}).status_code == 302
    assert len(response(app)[3]["_more"]) == 1                                         # the page counted once
    p.get(url)
    p.post(url, data={"pos": "r2", "nav": "next", "i4": "fertig"})
    row = _rows(client)[0]
    assert row["time_more"] == "1" and row["time_up"] == "1"
    codebook = client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    assert "time_more" in codebook and "WCAG 2.2.1" in codebook


def test_a_time_that_belongs_to_the_experiment_is_fixed_and_said_beforehand(app, client, survey):
    from app import pagetime

    time_setup(client, required=True, max_seconds=30, time_essential=1)
    with app.app_context():
        assert pagetime.essential(db.session.get(Block, 2))
    # the tick goes off and on again (saved at once, like the other ticks of the block)
    client.post("/surveys/1/editor/blocks/2", data={"title": "Block", "changed": "time_essential"})
    with app.app_context():
        assert not pagetime.essential(db.session.get(Block, 2)) and db.session.get(Block, 2).settings["max_seconds"] == 30
    client.post("/surveys/1/editor/blocks/2", data={"title": "Block", "time_essential": "1", "changed": "time_essential"})
    with app.app_context():
        assert pagetime.essential(db.session.get(Block, 2))
    editor = client.get("/surveys/1/editor/").data.decode()
    assert 'name="time_essential"' in editor and "Die Zeit gehört zum Versuch" in editor
    p, url = start(app, client)
    html = p.get(url).data.decode()
    assert "data-hold-more" not in html and 'data-more="0"' in html
    assert "Für diese Seite haben Sie 30 Sekunden Zeit." in html
    p.post(url, data={"pos": "r1", "nav": "next", "time_more": "1", "i2": ""})      # nothing to extend
    assert "_more" not in response(app)[3]
    wait(app, 30)
    assert p.post(url, data={"pos": "r1", "nav": "next", "time_up": "1", "time_more": "1", "i2": ""}).status_code == 302
    p.get(url)
    p.post(url, data={"pos": "r2", "nav": "next", "i4": "fertig"})
    assert "time_more" not in _rows(client)[0]                                         # no page could be extended
    # the tick travels with the file
    assert pagetime.clean({"max_seconds": 30, "time_essential": True}) == {"max_seconds": 30, "time_essential": True}
    assert pagetime.clean({"max_seconds": 30, "time_essential": "ja"}) == {"max_seconds": 30}


def test_the_page_script_offers_more_time_twenty_seconds_before_the_end():
    js = _js()
    assert 'body.append("nav", "time_more")' in js and "max += extra" in js and "left(max) <= 21" in js


# --- D2: mark an image with the keyboard -----------------------------------------------------------------------------

def test_spots_can_be_marked_with_the_keyboard(app, client, survey):
    mark_setup(client, app)
    html = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    assert "p-mark-cross" in html and "data-mark-list" in html and "data-mark-status" in html
    assert 'id="mark-keys-2"' in html and "Pfeiltasten" in html
    assert 'aria-live="polite" data-mark-count' not in html                           # the status line says it once
    js = _js()
    for part in ('stage.tabIndex = 0', 'e.shiftKey ? 0.1 : 0.02', 'e.key === "Enter"', 'e.key === "Delete"',
                 '"Stelle {n} gesetzt, {where}."', '"{x} % von links, {y} % von oben"', '"Stelle {n} entfernen"'):
        assert part in js, part


def test_free_hand_lines_keep_the_pointer_only(app, client, survey):
    mark_setup(client, app, mode="lines")
    html = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    assert "p-mark-cross" not in html and "data-mark-list" not in html and "mark-keys-2" not in html


# --- D7: timed tasks and pages that move on by themselves -------------------------------------------------------------

def test_pages_without_next_say_that_they_move_on(app, client, survey):
    from tests.quotas.test_assign import setup as assign_setup

    assign_setup(client)
    with app.app_context():
        item = db.session.get(Item, 2)
        item.config = {**item.config, "auto_next": True}
        db.session.commit()
    html = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    assert "data-auto-next" in html and "data-auto-note" in html
    assert "Nach der Aufgabe geht es automatisch weiter." in html
    assert 'data-assign-task tabindex="-1"' in html


def test_focus_and_announcements_of_timed_tasks(app):
    js = _js()
    assert "if (keys) task.focus(); else left.focus();" in js                        # assignment after "Start"
    assert "sooner.focus();" in js and '"Neue Wahl {n} von {m}: {s} oder {l}."' in js  # discounting
    assert "go.focus();" in js and "task.focus();" in js                             # IAT rounds
    assert '"Schritt 2 von 2: Bewerten Sie jetzt Ihre Begriffe."' in js and "legend.focus(" in js
    assert '"Aufnahme läuft, höchstens {t}."' in js and '"Aufnahme beendet, {t}."' in js
    voice = open(os.path.join(ROOT, "app/templates/items/voice/participant.html"), encoding="utf-8").read()
    assert 'data-voice-time aria-live' not in voice and "data-voice-status" in voice
    with app.app_context():
        from app.ui_i18n import JS_TEXTS, for_js

        assert "Spot {n} marked, {where}." in JS_TEXTS and for_js("es")["Spot {n} removed."] == "Punto {n} eliminado."
        assert for_js("de-du")["Step 2 of 2: now rate your words."].startswith("Schritt 2 von 2: Bewerte")


def test_association_has_a_status_line_for_step_two(app, client, survey):
    from tests.conftest import add

    add(client, "association")                                                         # item 2
    html = client.get("/surveys/1/editor/preview?page=2", follow_redirects=True).data.decode()
    assert "data-assoc-status" in html and 'class="p-assoc-legend" tabindex="-1"' in html


def test_more_time_can_be_taken_again_and_stops_at_the_limit(app, client, survey):
    from app import pagetime

    time_setup(client, required=True, max_seconds=30)
    p, url = start(app, client)
    p.get(url)
    p.post(url, data={"pos": "r1", "nav": "next", "time_more": "3", "i2": ""})       # three times on the page
    data = response(app)[3]
    assert data["_hold"]["more"] == 3 and len(data["_more"]) == 1
    again = p.post(url, data={"pos": "r1", "nav": "next", "time_more": "99", "i2": ""}).data.decode()
    assert response(app)[3]["_hold"]["more"] == pagetime.MORE_LIMIT                    # never more than ten
    assert "data-hold-more" not in again and 'name="time_more" value="10"' in again
    wait(app, 30 * 10)
    assert p.post(url, data={"pos": "r1", "nav": "next", "time_up": "1", "time_more": "10", "i2": ""}).status_code == 200
    wait(app, 30)
    assert p.post(url, data={"pos": "r1", "nav": "next", "time_up": "1", "time_more": "10", "i2": ""}).status_code == 302
