"""Minimum and maximum time of a page (app/pagetime.py), set per block."""
import csv
import io
import json
import re
from datetime import datetime, timedelta

from app import pagetime
from tests.participation.test_participate import _pos
from app.extensions import db
from app.models import Block, Response, Survey
from tests.conftest import CONSENT_OK, add, fill_consent
from tests.helpers.page_pos import page_pos

META = {"title": "Test", "languages": ["de"], "default_language": "de", "allow_back": "1"}


def setup(client, pages=2, required=False, **times):
    """Questions "q1", "q2", ... (items 2, 4, ...), each on a page of its own in block 2, which has the times."""
    fill_consent(client)
    for n in range(pages):
        if n:
            add(client, "page_break")
        add(client, "text_input")
        client.post(f"/surveys/1/editor/items/{2 + 2 * n}", data={"code": f"q{n + 1}", "question": "Frage", **({"answer_mode": "required"} if required else {})})
    client.post("/surveys/1/meta", data=META)
    return client.post("/surveys/1/editor/blocks/2", data={"title": "Block", **{k: str(v) for k, v in times.items()}}, follow_redirects=True)


def start(app, client):
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        uid = db.session.get(Survey, 1).code
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK, follow_redirects=True)
    return p, f"/s/{uid}"


def wait(app, seconds):
    """Pretend the page has been on screen for a while."""
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(is_preview=False))
        hold = dict(r.paradata["_hold"])
        hold["at"] = (datetime.fromisoformat(hold["at"]) - timedelta(seconds=seconds)).isoformat()
        r.paradata = {**r.paradata, "_hold": hold}
        db.session.commit()


def response(app):
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(is_preview=False))
        return r.status, r.position, dict(r.answers or {}), dict(r.paradata or {})


def test_times_are_saved_at_the_block_and_shown_in_the_editor(app, client, survey):
    page = setup(client, min_seconds=10, max_seconds=60).data.decode()
    with app.app_context():
        assert pagetime.of(db.session.get(Block, 2)) == {"min": 10, "max": 60}
    assert 'name="min_seconds"' in page and 'value="10"' in page and 'value="60"' in page
    # an empty field takes the time away again; only the field named in "changed" is set
    client.post("/surveys/1/editor/blocks/2", data={"title": "Block", "min_seconds": "", "max_seconds": "", "changed": "min_seconds"})
    with app.app_context():
        assert pagetime.of(db.session.get(Block, 2)) == {"min": None, "max": 60}


def test_a_timer_must_run_longer_than_next_is_held(app, client, survey):
    page = setup(client, min_seconds=30, max_seconds=20).data.decode()
    assert "Höchstzeit muss länger sein" in page
    with app.app_context():
        assert pagetime.of(db.session.get(Block, 2)) == {"min": None, "max": None}


def test_out_of_range_and_words_are_not_taken(app, client, survey):
    setup(client, min_seconds=100000, max_seconds="bald")
    with app.app_context():
        assert pagetime.of(db.session.get(Block, 2)) == {"min": pagetime.MIN_LIMIT, "max": None}


def test_next_is_held_until_the_minimum_time_is_over(app, client, survey):
    setup(client, min_seconds=10)
    p, url = start(app, client)
    page = p.get(url).data.decode()
    assert 'data-hold data-min="10" data-max="0"' in page
    early = p.post(url, data={"pos": page_pos(p, 1), "nav": "next", "i2": "Antwort"})
    assert early.status_code == 200 and "bleibt noch" in early.data.decode()   # the page once more, with a note
    assert response(app)[1:3] == (1, {"1": "1", "2": "Antwort"})               # ... and what was typed is kept
    wait(app, 10)
    assert p.post(url, data={"pos": _pos(early.get_data(as_text=True)), "nav": "next", "i2": "Antwort"}).status_code == 302
    assert response(app)[1] == 2
    # the next page of the block has its own clock
    assert 'data-min="10"' in p.get(url).data.decode()
    assert p.post(url, data={"pos": page_pos(p, 2), "nav": "next", "i4": "x"}).status_code == 200


def test_loading_the_page_again_does_not_start_the_clock_anew(app, client, survey):
    setup(client, min_seconds=10)
    p, url = start(app, client)
    p.get(url)
    wait(app, 6)
    assert 'data-min="4"' in p.get(url).data.decode()


def test_back_is_never_held_and_the_clock_starts_anew_on_return(app, client, survey):
    setup(client, min_seconds=10)
    p, url = start(app, client)
    p.get(url)
    wait(app, 10)
    p.post(url, data={"pos": page_pos(p, 1), "nav": "next", "i2": "a"})
    p.get(url)
    assert p.post(url, data={"pos": page_pos(p, 2), "nav": "back"}).status_code == 302   # at once
    assert response(app)[1] == 1
    assert 'data-min="10"' in p.get(url).data.decode()


def test_the_timer_sends_the_page_without_asking_for_required_answers(app, client, survey):
    setup(client, required=True, max_seconds=30)
    p, url = start(app, client)
    # the clock runs from the consent on: under load a second or two may pass before the page comes (data-max="29")
    shown = re.search(r'data-min="0" data-max="(\d+)"', p.get(url).data.decode())
    assert shown and 25 <= int(shown.group(1)) <= 30
    # a page that only claims its time is up is an ordinary "Next": the required answer is asked for
    claimed = p.post(url, data={"pos": page_pos(p, 1), "nav": "next", "time_up": "1", "i2": ""})
    assert claimed.status_code == 200 and "has-error" in claimed.data.decode()
    wait(app, 30)
    assert p.post(url, data={"pos": _pos(claimed.get_data(as_text=True)), "nav": "next", "time_up": "1", "i2": ""}).status_code == 302
    status, position, answers, data = response(app)
    assert position == 2 and not answers.get("2") and len(data["_timeup"]) == 1 and "_hold" not in data
    # the person answers the second page in time: an ordinary "Next"
    p.get(url)
    p.post(url, data={"pos": page_pos(p, 2), "nav": "next", "i4": "fertig"})
    assert response(app)[0] == Response.COMPLETED
    row = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))[0]
    assert row["time_up"] == "1" and row["q1"] == "" and row["q2"] == "fertig"
    codebook = client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    assert "time_up" in codebook and "30 s" in codebook


def test_without_a_timer_there_is_no_column(app, client, survey):
    setup(client, min_seconds=5)
    start(app, client)
    assert "time_up" not in client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig").splitlines()[0]


def test_the_consent_is_never_timed(app, client, survey):
    fill_consent(client)
    add(client, "text_input")
    # a question text: without it the start failed, and the test passed on the closed page of a draft (audit 2026-10-06)
    client.post("/surveys/1/editor/items/2", data={"code": "q1", "question": "Frage 1"})
    client.post("/surveys/1/meta", data=META)
    with app.app_context():
        block = db.session.get(Block, 1)
        block.settings = {**(block.settings or {}), "min_seconds": 20}
        db.session.commit()
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        assert db.session.get(Survey, 1).status == Survey.RUNNING
        uid = db.session.get(Survey, 1).code
    p = app.test_client()
    assert "data-hold" not in p.get(f"/s/{uid}").data.decode()
    assert p.post(f"/s/{uid}", data=CONSENT_OK).status_code == 302


def test_dummy_data_passes_timed_pages(app, client, survey):
    setup(client, min_seconds=30, max_seconds=60)
    client.post("/surveys/1/dummy", data={"n": "3"})
    with app.app_context():
        done = db.session.scalars(db.select(Response).filter_by(source="dummy")).all()
        assert len(done) == 3 and all(r.status == Response.COMPLETED for r in done)


def test_times_travel_with_the_file_and_the_copy(app, client, survey):
    setup(client, min_seconds=10, max_seconds=60)
    data = json.loads(client.get("/surveys/1/export/survey.json").data)
    assert data["blocks"][1]["settings"]["min_seconds"] == 10
    assert pagetime.clean({"min_seconds": 10, "max_seconds": "60", "other": 1}) == {"min_seconds": 10}
    assert pagetime.clean({"min_seconds": 40, "max_seconds": 20}) == {"min_seconds": 40}
