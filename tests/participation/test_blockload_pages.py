"""A simple block loaded at once (app/blockload.py, participate.block_page, _finish_block): all pages in one document,
each page stored in the background, the whole block checked again at its end, page times measured by the browser and
checked against the server's clock (block-load concept 2026-10-15, part 2, decisions 3 and 4a)."""
import json
import os
import re
from datetime import timedelta

from app import blockload
from app.extensions import db
from app.models import Block, Response, Survey, utcnow
from tests.conftest import CONSENT_OK, add, fill_consent

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))


def build(client, app, block_load=True, page_timing=False, min_seconds=None, **survey):
    """consent | block 2: text a (required), page break, text b, page break, text c | block 3: text d"""
    fill_consent(client)
    add(client, "text_input")                                       # 2
    client.post("/surveys/1/editor/items/2", data={"code": "a", "question": "Frage A", "answer_mode": "required"})
    add(client, "page_break")                                       # 3
    add(client, "text_input")                                       # 4
    client.post("/surveys/1/editor/items/4", data={"code": "b", "question": "Frage B", "answer_mode": "required"})
    add(client, "page_break")                                       # 5
    add(client, "text_input")                                       # 6
    client.post("/surveys/1/editor/items/6", data={"code": "c", "question": "Frage C"})
    client.post("/surveys/1/editor/blocks")                         # block 3
    add(client, "text_input", block=3)                              # 7
    client.post("/surveys/1/editor/items/7", data={"code": "d", "question": "Frage D"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.block_load, s.page_timing = block_load, page_timing
        for key, value in survey.items():
            setattr(s, key, value)
        if min_seconds:
            block = db.session.get(Block, 2)
            block.settings = {**(block.settings or {}), "min_seconds": min_seconds}
        db.session.commit()
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.status == Survey.RUNNING
        return "/s/" + s.code


def start(app, url):
    p = app.test_client()
    p.get(url)
    p.post(url, data=CONSENT_OK)
    return p


def response(app):
    with app.app_context():
        r = db.session.scalar(db.select(Response))
        db.session.expunge(r)
        return r


def sections(html):
    return re.findall(r'<section class="p-bpage" data-bpage="(\d+)"[^>]*?(hidden)?>', html)


def store(p, url, page, seq, form, **extra):
    return p.post(f"{url}/block", data=json.dumps({"start": 1, "page": page, "seq": seq, "form": form, **extra}),
                  content_type="application/json")


def final(p, url, token, form, pages, start=1):
    meta = {"start": start, "pages": {str(k): {"ms": ms} for k, ms in pages.items()}}
    return p.post(url, data={**form, "pos": token, "nav": "next", "_block": json.dumps(meta)})


def test_simple_block_comes_in_one_document(app, client, survey):
    url = build(client, app)
    p = start(app, url)
    html = p.get(url).get_data(as_text=True)
    assert "data-block " in html and 'data-block-url="' + url + '/block"' in html
    assert sections(html) == [("1", ""), ("2", "hidden"), ("3", "hidden")]
    assert "Frage A" in html and "Frage B" in html and "Frage C" in html and "Frage D" not in html
    # every page keeps the trap field, the page knows its position and says the change (live region)
    assert html.count('name="more_info"') == 3 and "data-bpage-live" in html
    assert 'data-announce="Seite 2 von etwa' in html
    with app.app_context():
        assert db.session.scalar(db.select(Response)).layout["bl"]["start"] == 1


def test_nothing_changes_without_the_setting_or_for_a_block_that_is_not_simple(app, client, survey):
    url = build(client, app, block_load=False)
    p = start(app, url)
    html = p.get(url).get_data(as_text=True)
    assert "data-block" not in html and "p-bpage" not in html and "Frage B" not in html


def test_block_with_a_dependency_goes_page_by_page(app, client, survey):
    url = build(client, app)
    with app.app_context():
        from app.models import Item

        item = db.session.get(Item, 4)
        item.config = {**item.config, "when": {"item": 2, "op": "filled", "value": ""}}
        s = db.session.get(Survey, 1)
        db.session.commit()
    p = start(app, url)
    html = p.get(url).get_data(as_text=True)
    assert "data-block" not in html and "Frage B" not in html


def test_pages_stored_in_the_background_idempotent(app, client, survey):
    url = build(client, app)
    p = start(app, url)
    p.get(url)
    r = store(p, url, 1, 100, {"i2": ["eins"]})
    assert r.status_code == 200 and r.json["ok"]
    assert response(app).answers["2"] == "eins"
    # the same page with the same counter again (from the queue of the tab): nothing changes
    assert store(p, url, 1, 100, {"i2": ["anders"]}).json["again"]
    assert response(app).answers["2"] == "eins"
    # a newer counter stores the newer answer
    assert store(p, url, 1, 101, {"i2": ["zwei"]}).json["ok"]
    assert response(app).answers["2"] == "zwei"
    # a required question left open: the error comes back, the page is not done
    out = store(p, url, 2, 102, {}).json
    assert not out["ok"] and "4" in out["errors"]
    assert "2" not in (response(app).layout["bl"]["done"])
    # a page outside the block, or a participation elsewhere: the browser loads the page anew
    assert store(p, url, 5, 103, {}).status_code == 409
    assert p.post(f"{url}/block", data=json.dumps({"start": 4, "page": 1, "seq": 1, "form": {}}),
                  content_type="application/json").status_code == 409


def test_reload_resumes_after_the_last_confirmed_page(app, client, survey):
    url = build(client, app)
    p = start(app, url)
    p.get(url)
    store(p, url, 1, 100, {"i2": ["eins"]})
    html = p.get(url).get_data(as_text=True)
    assert sections(html) == [("1", "hidden"), ("2", ""), ("3", "hidden")]
    assert 'value="eins"' in html


def test_end_of_block_checks_every_page_again(app, client, survey):
    url = build(client, app)
    p = start(app, url)
    p.get(url)
    # the background sends were lost: page B is empty; the server goes back to it with its error
    r = final(p, url, "r1", {"i2": "eins", "i6": "drei"}, {1: 2000, 2: 2000, 3: 2000})
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and sections(html) == [("1", "hidden"), ("2", ""), ("3", "hidden")]
    assert "has-error" in html
    rs = response(app)
    assert rs.position == 2 and rs.answers["2"] == "eins"
    # sent again, complete, with the token of the page the server is at: on to the next block
    r = final(p, url, "r2", {"i2": "eins", "i4": "zwei", "i6": "drei"}, {1: 2000, 2: 3000, 3: 2000})
    assert r.status_code in (302, 303)
    html = p.get(url).get_data(as_text=True)
    assert "Frage D" in html and "data-block" not in html
    rs = response(app)
    assert rs.answers["4"] == "zwei" and rs.answers["6"] == "drei" and "bl" not in rs.layout


def test_back_and_without_script(app, client, survey):
    url = build(client, app)
    p = start(app, url)
    p.get(url)
    # without JavaScript the block goes page by page: the token is that of the page the server is at
    p.post(url, data={"pos": "r1", "nav": "next", "i2": "eins"})
    html = p.get(url).get_data(as_text=True)
    assert 'name="pos" value="r2"' in html and sections(html)[1] == ("2", "")
    # "Back" from the block's first page with the whole block: the page before it (the consent stays where it is)
    meta = json.dumps({"start": 1, "pages": {}})
    p.post(url, data={"pos": "r2", "nav": "back", "_block": meta})
    # exactly the page before the block; before, the token of the page the server was at did not fit the block's
    # first page, and "Back" was dropped as a page sent twice (code review 2026-10-15, F2 and L3)
    assert response(app).position == 0


def _failed_check(app, url):
    """The end of the block found page B empty: the server is at page B (token r2), the block is sent from there."""
    p = start(app, url)
    p.get(url)
    final(p, url, "r1", {"i2": "eins", "i6": "drei"}, {1: 2000, 2: 2000, 3: 2000})
    assert response(app).position == 2
    return p, json.dumps({"start": 1, "pages": {}})


def test_back_after_a_failed_check_of_the_block(app, client, survey):
    """Code review 2026-10-15, F2: "Back" from the block after its check failed goes back, without the note that
    the page was sent twice."""
    url = build(client, app)
    p, meta = _failed_check(app, url)
    p.post(url, data={"pos": "r2", "nav": "back", "_block": meta, "i2": "zwei"})
    assert response(app).position == 0
    assert 'class="p-tempo-note"' not in p.get(url).get_data(as_text=True)


def test_language_after_a_failed_check_of_the_block(app, client, survey):
    """Code review 2026-10-15, F2: the language switch after the block's check failed keeps the inputs of the page
    and shows the block again in the language picked, without the note that the page was sent twice."""
    url = build(client, app)
    p, meta = _failed_check(app, url)
    p.post(url, data={"pos": "r2", "nav": "next", "set_lang": "en", "_block": meta, "i2": "zwei"})
    rs = response(app)
    assert rs.position == 1 and rs.language == "en" and rs.answers["2"] == "zwei"
    assert 'class="p-tempo-note"' not in p.get(url).get_data(as_text=True)


def test_page_times_from_the_browser_cut_to_the_server_clock(app, client, survey):
    url = build(client, app, page_timing=True)
    p = start(app, url)
    p.get(url)
    with app.app_context():  # the block was sent ten seconds ago
        r = db.session.scalar(db.select(Response))
        r.layout = {**r.layout, "bl": {**r.layout["bl"], "at": (utcnow() - timedelta(seconds=10)).isoformat()}}
        db.session.commit()
    # 3 × 20 s claimed by the browser, but the server saw only 10 s
    final(p, url, "r1", {"i2": "eins", "i4": "zwei", "i6": "drei"}, {1: 20000, 2: 20000, 3: 20000})
    rs = response(app)
    times = rs.paradata["_t"]
    assert 9 <= sum(times.values()) <= 11
    assert blockload.pages_timed(rs) == 3 and blockload.times_cut(rs) == 1
    # a plausible sum stays as the browser measured it
    with app.app_context():
        assert blockload.quality_counts(db.session.get(Survey, 1)) == {"timed": 0, "cut": 0}  # not completed yet


def test_plausible_browser_times_are_kept(app, client, survey):
    url = build(client, app, page_timing=True)
    p = start(app, url)
    p.get(url)
    with app.app_context():
        r = db.session.scalar(db.select(Response))
        r.layout = {**r.layout, "bl": {**r.layout["bl"], "at": (utcnow() - timedelta(seconds=60)).isoformat()}}
        db.session.commit()
    final(p, url, "r1", {"i2": "eins", "i4": "zwei", "i6": "drei"}, {1: 4000, 2: 6000, 3: 8000})
    rs = response(app)
    assert [round(rs.paradata["_t"][k]) for k in ("2", "4", "6")] == [4, 6, 8]
    assert blockload.times_cut(rs) == 0


def test_minimum_time_held_by_the_block_sum(app, client, survey):
    url = build(client, app, min_seconds=5)
    p = start(app, url)
    p.get(url)
    with app.app_context():
        r = db.session.scalar(db.select(Response))
        r.layout = {**r.layout, "bl": {**r.layout["bl"], "at": (utcnow() - timedelta(seconds=8)).isoformat()}}
        db.session.commit()
    # 3 × 6 s claimed, 8 s seen by the server: cut to about 2.7 s each, the first page was left too early
    r = final(p, url, "r1", {"i2": "eins", "i4": "zwei", "i6": "drei"}, {1: 6000, 2: 6000, 3: 6000})
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "bleibt noch" in html and response(app).position == 1


def test_export_has_the_browser_columns(app, client, survey):
    url = build(client, app, page_timing=True)
    p = start(app, url)
    p.get(url)
    with app.app_context():
        r = db.session.scalar(db.select(Response))
        r.layout = {**r.layout, "bl": {**r.layout["bl"], "at": (utcnow() - timedelta(seconds=60)).isoformat()}}
        db.session.commit()
    final(p, url, "r1", {"i2": "eins", "i4": "zwei", "i6": "drei"}, {1: 4000, 2: 6000, 3: 8000})
    p.post(url, data={"pos": "r4", "nav": "next", "i7": "vier"})
    with app.app_context():
        assert db.session.scalar(db.select(Response)).status == Response.COMPLETED
        assert blockload.quality_counts(db.session.get(Survey, 1)) == {"timed": 1, "cut": 0}
    csv = client.get("/surveys/1/export/data.csv").get_data(as_text=True)
    head = csv.splitlines()[0]
    assert "pages_browser" in head and "times_cut" in head
    assert "Einfache Blöcke werden am Stück geladen" in client.get("/surveys/1/quality").get_data(as_text=True)


def test_privacy_texts_name_the_buffer(app, client, survey):
    url = build(client, app)
    html = app.test_client().get(url + "/privacy").get_data(as_text=True)
    assert "sessionStorage" in html
    with app.app_context():  # and the consent's standard text (items/consent.py)
        consent = db.session.get(Survey, 1).consent_item
        assert "sessionStorage" in consent.kind.template_body(consent, "de")


def test_script_parts_of_the_block():
    js = open(os.path.join(ROOT, "app/static/participant.js"), encoding="utf-8").read()
    block = js[js.index("var blockPages = null"):js.index("One page, one submit")]
    assert '"X-CSRFToken": csrf' in block and "keepalive: true" in block and "sessionStorage" in block
    assert 'addEventListener("pagehide"' in block and 'addEventListener("online", flush)' in block
    assert "heading.focus" in block and "data-announce" in block  # focus to the heading, the change said
    assert 'field.name = "_block"' in block
    assert js.index("var blockPages = null") < js.index("One page, one submit")  # before the lock of the form
    assert "function holdPage(root)" in js and 'document.addEventListener("p:page"' in js


def test_block_entered_again_from_the_next_block(app, client, survey):
    """"Back" from the next block shows the block on its last page; sent again, the pages before keep the time they
    had (their minimum is not asked for again) and their time is not counted twice."""
    url = build(client, app, page_timing=True, min_seconds=3)
    p = start(app, url)
    p.get(url)
    with app.app_context():
        r = db.session.scalar(db.select(Response))
        r.layout = {**r.layout, "bl": {**r.layout["bl"], "at": (utcnow() - timedelta(seconds=30)).isoformat()}}
        db.session.commit()
    final(p, url, "r1", {"i2": "eins", "i4": "zwei", "i6": "drei"}, {1: 4000, 2: 4000, 3: 4000})
    assert response(app).position == 4
    p.post(url, data={"pos": "r4", "nav": "back"})
    html = p.get(url).get_data(as_text=True)
    # page 3 was left once, so a form of it from before no longer fits (audit 2026-10-17, D-01): "r3.1"
    assert sections(html) == [("1", "hidden"), ("2", "hidden"), ("3", "")] and 'name="pos" value="r3.1"' in html
    with app.app_context():
        r = db.session.scalar(db.select(Response))
        r.layout = {**r.layout, "bl": {**r.layout["bl"], "at": (utcnow() - timedelta(seconds=10)).isoformat()}}
        db.session.commit()
    # only page 3 was on screen in this document, for 4 s
    r = final(p, url, "r3.1", {"i2": "eins", "i4": "zwei", "i6": "drei"}, {3: 4000})
    assert r.status_code in (302, 303) and response(app).position == 4
    times = response(app).paradata["_t"]
    assert [round(times[k]) for k in ("2", "4", "6")] == [4, 4, 8]


def test_an_older_send_of_a_page_never_overwrites_a_newer_one(app, client, survey):
    """Audit data storage 2026-10-21, A4: a page queued in the tab (seq 1) and later sent again with "Back" (seq 2,
    which never marks the page done) must not be overwritten when the old copy arrives from the queue."""
    url = build(client, app)
    p = start(app, url)
    p.get(url)
    assert store(p, url, 2, 2, {"i4": ["neu"]}, back=1).get_json()["ok"]
    assert store(p, url, 2, 1, {"i4": ["alt"]}).get_json().get("again")
    assert response(app).answers["4"] == "neu"
    # a newer send that came back with an error: an older one is still refused
    store(p, url, 1, 5, {"i2": [""]})   # the required question left empty
    assert store(p, url, 1, 4, {"i2": ["alt"]}).get_json().get("again")
    assert "2" not in response(app).answers


def test_a_block_field_nested_too_deep_is_ignored(app, client, survey):
    """Gesamtaudit 2026-10-22, C-02: "[" * 5000 in the field _block raised RecursionError (a 500) for anyone with the
    link; now it counts as missing."""
    from app import blockload
    from werkzeug.datastructures import MultiDict

    assert blockload.read_meta(MultiDict({"_block": "[" * 5000})) is None
