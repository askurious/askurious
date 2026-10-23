"""Answers fixed by feedback (app/feedback_lock.py; Philipp and Ralf, 2026-10-11): once the feedback has been seen,
the answers it shows can no longer be changed, so nobody moves towards the majority."""
from app.extensions import db
from app.models import Item, Response, Survey
from tests.conftest import CONSENT_OK, add, fill_consent


def _uid(app):
    with app.app_context():
        return db.session.get(Survey, 1).code


def _answers(app):
    with app.app_context():
        return db.session.scalar(db.select(Response)).answers


def build(client, same_page=False, lock=True):
    """Block 2: a rating 1..5 (2), then a crowd (3) on the next page or on the same page, then an info (5)."""
    fill_consent(client)
    add(client, "rating")                                                                 # 2
    client.post("/surveys/1/editor/items/2", data={"code": "r", "question": "Wie sehr?", "style": "numbers",
                                                   "min": "1", "max": "5", "answer_mode": "required"})
    if not same_page:
        add(client, "page_break")
    crowd = 3 if same_page else 4
    add(client, "sample_crowd")
    client.post(f"/surveys/1/editor/items/{crowd}", data={"code": "menge", "question": "Andere", "source": "2",
                                                          "show_own": "1", "avatars": "40",
                                                          **({"lock_answers": "1"} if lock else {})})
    add(client, "page_break")
    add(client, "info")
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    return crowd


def test_new_feedback_elements_fix_answers_by_default(app, client, survey):
    fill_consent(client)
    for key in ("sample_compare", "sample_crowd", "score_band", "scale_profile", "topic_map", "topic_profile"):
        add(client, key)
    with app.app_context():
        items = db.session.scalars(db.select(Item).where(Item.type != "consent")).all()
        assert items and all(i.config.get("lock_answers") is True for i in items)
    html = client.get("/surveys/1/editor/items/2").data.decode()
    assert 'name="lock_answers"' in html and "Antworten danach nicht mehr ändern" in html


def test_feedback_on_the_next_page_fixes_the_answer(app, client, survey):
    build(client)
    url = f"/s/{_uid(app)}"
    p = app.test_client()
    p.get(url)
    p.post(url, data=dict(CONSENT_OK))
    first = p.get(url).data.decode()
    assert "sobald Sie die Rückmeldung dazu gesehen haben" in first                     # said beforehand
    p.post(url, data={"nav": "next", "i2": "3"})
    assert "Andere" in p.get(url).data.decode()                                          # the crowd was shown
    p.post(url, data={"nav": "back"})
    back = p.get(url).data.decode()
    assert "p-fixed" in back and " inert" not in back and "festgelegt und lässt sich nicht mehr ändern" in back
    p.post(url, data={"nav": "next", "i2": "5"})                                         # a changed answer is dropped
    assert _answers(app)["2"] == 3
    p.post(url, data={"nav": "back"})
    p.post(url, data={"nav": "next"})                                                    # sent without it: still 3
    assert _answers(app)["2"] == 3


def test_without_the_tick_the_answer_stays_open(app, client, survey):
    build(client, lock=False)
    url = f"/s/{_uid(app)}"
    p = app.test_client()
    p.get(url)
    p.post(url, data=dict(CONSENT_OK))
    assert "Rückmeldung dazu gesehen" not in p.get(url).data.decode()
    p.post(url, data={"nav": "next", "i2": "3"})
    p.get(url)
    p.post(url, data={"nav": "back"})
    assert "p-fixed" not in p.get(url).data.decode()
    p.post(url, data={"nav": "next", "i2": "5"})
    assert _answers(app)["2"] == 5


def test_crowd_on_the_same_page_waits_for_fixing(app, client, survey):
    crowd = build(client, same_page=True)
    url = f"/s/{_uid(app)}"
    p = app.test_client()
    p.get(url)
    p.post(url, data=dict(CONSENT_OK))
    page = p.get(url).data.decode()
    assert "data-crowd-fix-button" in page and "Antwort festlegen" in page and '"lock": true' in page
    assert "p-fixed" not in page                                                         # open until it is sent
    p.post(url, data={"nav": "next", "i2": "2", f"i{crowd}_n_2": "0"})
    p.get(url)
    p.post(url, data={"nav": "back"})
    back = p.get(url).data.decode()
    assert "festgelegt und lässt sich nicht mehr ändern" in back
    p.post(url, data={"nav": "next", "i2": "4", f"i{crowd}_n_2": "0"})
    assert _answers(app)["2"] == 2


def test_unanswered_question_on_the_same_page_stays_open(app, client, survey):
    """Sent without an answer (an optional question): nothing to fix, the question can still be answered."""
    crowd = build(client, same_page=True)
    with app.app_context():
        db.session.get(Item, 2).answer_mode = "optional"
        db.session.commit()
    url = f"/s/{_uid(app)}"
    p = app.test_client()
    p.get(url)
    p.post(url, data=dict(CONSENT_OK))
    p.post(url, data={"nav": "next", f"i{crowd}_n_2": "0"})
    p.get(url)
    p.post(url, data={"nav": "back"})
    assert "p-fixed" not in p.get(url).data.decode()
    p.post(url, data={"nav": "next", "i2": "4", f"i{crowd}_n_2": "0"})
    assert _answers(app)["2"] == 4


def _crowd_data(page):
    import html
    import json
    import re

    return json.loads(html.unescape(re.search(r"data-crowd='([^']*)'", page).group(1)))


def test_crowd_on_the_same_page_gets_its_figures_only_from_the_server(app, client, survey):
    """Audit 2026-10-11 (N8/TN4): before the answer is fixed, the page holds no figures of the others; "Antwort
    festlegen" sends the answer, the server stores and fixes it and only then gives the figures; a changed answer
    sent with the page afterwards is dropped, as with feedback on a later page."""
    crowd = build(client, same_page=True)
    url = f"/s/{_uid(app)}"
    p = app.test_client()
    p.get(url)
    p.post(url, data=dict(CONSENT_OK))
    page = p.get(url).data.decode()
    data = _crowd_data(page)
    assert data["avatars"] == [] and data["own"] is None and data["fetch"] == f"{url}/crowd/{crowd}"
    assert p.post(data["fetch"], data={"pos": "r0", "i2": "1"}).status_code == 409       # not the page shown
    assert p.post(f"{url}/crowd/2", data={"pos": "r1", "i2": "1"}).status_code == 409    # no crowd
    missing = p.post(data["fetch"], data={"pos": "r1"})
    assert missing.status_code == 400 and missing.json["error"]
    assert "2" not in _answers(app)
    fixed = p.post(data["fetch"], data={"pos": "r1", "i2": "1"})
    assert fixed.status_code == 200 and len(fixed.json["avatars"]) == 40 and fixed.json["own"] is not None
    with app.app_context():
        r = db.session.scalar(db.select(Response))
        assert r.answers["2"] == 1 and "2" in r.layout["locked"]
    again = p.get(url).data.decode()                                                     # a reload: fixed, figures in the page
    assert "festgelegt und lässt sich nicht mehr ändern" in again and len(_crowd_data(again)["avatars"]) == 40
    assert p.post(data["fetch"], data={"pos": "r1", "i2": "5"}).json["avatars"]          # once more: still 1
    p.post(url, data={"nav": "next", "i2": "5", f"i{crowd}_n_2": "0"})                  # the page sent with another one
    assert _answers(app)["2"] == 1


def test_crowd_without_the_tick_shows_its_figures_at_once(app, client, survey):
    build(client, same_page=True, lock=False)
    url = f"/s/{_uid(app)}"
    p = app.test_client()
    p.get(url)
    p.post(url, data=dict(CONSENT_OK))
    data = _crowd_data(p.get(url).data.decode())
    assert len(data["avatars"]) == 40 and data["fetch"] is None


def _fixed(app, item_id, answer, lang="de"):
    """The participant's view of one element whose answer is fixed (participant/_item.html)."""
    from flask import render_template_string

    with app.test_request_context("/"):
        item = db.session.get(Item, item_id)
        return render_template_string('{% include "participant/_item.html" %}', item=item, survey=item.survey,
                                      lang=lang, answers={item.id: answer}, errors={}, variables={},
                                      locked_items={item.id})


def test_a_fixed_answer_that_cannot_be_piped_is_said_in_words(app, client, survey):
    """UI/UX audit 2026-10-17, W-A03: a fixed answer of a type that cannot be piped (marked image, Q-sort, map) keeps
    its disabled controls and gets the saved answer in words below them, so a screen reader reads more than greyed
    controls; in the participant's language."""
    fill_consent(client)
    add(client, "image_mark")                                                            # 2
    add(client, "q_sort")                                                                # 3
    add(client, "map_marker")                                                            # 4
    with app.app_context():
        mark = db.session.get(Item, 2)
        mark.config = {**mark.config, "mode": "rate", "areas": [{"id": "a1", "code": "1", "box": [0, 0, .5, .5]},
                                                                {"id": "a2", "code": "2", "box": [.5, .5, .5, .5]}]}
        texts = dict(mark.texts or {})
        texts["de"] = {**texts.get("de", {}), "areas": {"a1": "Dach", "a2": "Tür"}}
        mark.texts = texts
        db.session.commit()
        html = _fixed(app, 2, {"1": 1, "2": -1})
        assert '<fieldset disabled class="p-fixed-fields">' in html and "Ihre gespeicherte Antwort:" in html
        assert "<dt>Dach</dt><dd>gefällt</dd>" in html and "<dt>Tür</dt><dd>gefällt nicht</dd>" in html
        assert "Your saved answer:" in _fixed(app, 2, {"1": 1}, lang="en")
        mark.config = {**mark.config, "mode": "points"}
        db.session.commit()
        assert "Markierte Stellen: 3" in _fixed(app, 2, [[.1, .1], [.2, .2], [.3, .3]])
        assert "Ich kann hier nichts markieren" in _fixed(app, 2, [])
        assert "Keine Antwort" in _fixed(app, 2, None)

        qs = db.session.get(Item, 3)
        codes = [o["code"] for o in qs.config["options"]]
        html = _fixed(app, 3, {"q": {codes[0]: -1, codes[1]: 2}})
        summary = html.split('class="p-fixed-summary"', 1)[1]
        assert "<dd>+2</dd>" in summary and "<dd>-1</dd>" in summary
        assert summary.index("<dd>+2</dd>") < summary.index("<dd>-1</dd>")             # most agreement first

        html = _fixed(app, 4, [{"lat": 50.7, "lon": 6.1}, {"lat": 50.8, "lon": 6.2}])
        assert "Gesetzte Marker: 2" in html and "Gespeicherte" not in html


def test_a_pipeable_fixed_answer_stays_text_only(app, client, survey):
    """W-A03 (#1190): a pipeable answer shows only the question and its text, no disabled controls."""
    fill_consent(client)
    add(client, "rating")                                                                # 2
    with app.app_context():
        html = _fixed(app, 2, 4)
        assert "p-fixed-fields" not in html and "p-fixed-summary" not in html and "<p>4</p>" in html
