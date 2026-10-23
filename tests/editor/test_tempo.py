"""Tempo hint (app/tempo.py): a page sent faster than its questions can be read is shown once more with a note."""
import csv
import io
from datetime import datetime, timedelta

from app import tempo
from app.extensions import db
from app.models import Block, Item, Response, Survey
from tests.conftest import CONSENT_OK, add, fill_consent

TEN_WORDS = "Wie alt waren Sie an Ihrem letzten Geburtstag in Jahren"  # 10 words: threshold 3.5 s
META = {"title": "Test", "languages": ["de"], "default_language": "de", "allow_back": "1"}


def setup(client, pages=1, **settings):
    """Questions "q1", "q2", ... (items 2, 4, ...), each on a page of its own, and the hint switched on."""
    fill_consent(client)
    for n in range(pages):
        if n:
            add(client, "page_break")
        add(client, "text_input")
        client.post(f"/surveys/1/editor/items/{2 + 2 * n}", data={"code": f"q{n + 1}", "question": TEN_WORDS})
    client.post("/surveys/1/meta", data={**META, "tempo_hint": "all", **settings})


def start(app, client):
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        uid = db.session.get(Survey, 1).code
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    return p, f"/s/{uid}"


def wait(app, seconds):
    """Pretend the page has been on screen for a while."""
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(is_preview=False))
        shown = dict(r.paradata["_shown"])
        shown["at"] = (datetime.fromisoformat(shown["at"]) - timedelta(seconds=seconds)).isoformat()
        r.paradata = {**r.paradata, "_shown": shown}
        db.session.commit()


def state(app):
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(is_preview=False))
        kept = (r.paradata or {}).get("_tempo")
        return r.status, ({k: v for k, v in kept.items() if k != "pos"} if kept else kept)  # "pos": see the flow tests


def export_row(client):
    return list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))[0]


def test_words_that_have_to_be_read(app, client, survey):
    add(client, "single_choice")                                     # item 2
    add(client, "likert")                                            # item 3
    add(client, "ranking")                                           # item 4
    with app.app_context():
        choice, likert, ranking = (db.session.get(Item, n) for n in (2, 3, 4))
        choice.texts = {"de": {"question": "Wo wohnen **Sie** <u>heute</u>?", "options": {"a": "in einer großen Stadt"}}}
        likert.texts = {"de": {"question": "Wie sehr stimmen Sie zu?", "labels": ["stimme gar nicht zu", "stimme zu"],
                               "statements": {"s1": "Ich mag {{robot}}.", "s2": "Technik hilft mir."}}}
        ranking.texts = {"de": {"question": "Bitte ordnen:", "options": {"a": "Preis", "b": "gute Qualität"}}}
        assert tempo.words(choice, "de") == 4                        # answer options are scanned, not read
        assert tempo.words(likert, "de") == 5 + 3 + 3                # scale labels neither; "{{robot}}" is one word
        assert tempo.words(likert, "de", {"robot": "den kleinen Roboter"}) == 5 + 5 + 3
        assert tempo.words(ranking, "de") == 2 + 3                   # here the options are what is judged
        assert tempo.threshold([choice, likert], "de") == round(15 * 0.35, 1)


def test_off_by_default(app, client, survey):
    fill_consent(client)
    add(client, "text_input")
    client.post("/surveys/1/editor/items/2", data={"code": "q1", "question": TEN_WORDS})
    client.post("/surveys/1/meta", data={**META, "page_timing": "1"})
    p, url = start(app, client)
    p.get(url)
    assert p.post(url, data={"nav": "next", "i2": "40"}).status_code == 302  # sent at once, no note
    assert state(app) == (Response.COMPLETED, None)
    assert "tempo_checked" not in client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig").splitlines()[0]


def test_fast_page_is_shown_once_more_with_the_note(app, client, survey):
    setup(client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.page_timing and s.tempo_hint == "all" and s.tempo_dose == "once"  # the hint needs the page times
    p, url = start(app, client)
    p.get(url)
    wait(app, 2)                                                     # below 3.5 s
    r = p.post(url, data={"nav": "next", "i2": "40"})
    page = r.data.decode()
    assert r.status_code == 200 and "data-tempo-note" in page and "sehr schnell beantwortet" in page
    assert 'value="40"' in page                                     # the answer stays
    assert state(app) == (Response.ACTIVE, {"n": 1, "fast": 1, "hint": 1, "at": "2"})  # the consent is not checked
    assert p.post(url, data={"nav": "next", "i2": "41"}).status_code == 302  # the second "Next" goes on
    assert state(app)[0] == Response.COMPLETED
    row = export_row(client)
    assert (row["tempo_checked"], row["tempo_fast"], row["tempo_hint"], row["tempo_hint_at"]) == ("1", "1", "1", "q1")
    assert row["q1"] == "41"
    book = client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    assert "tempo_hint_at" in book and "350 ms je Wort" in book and "q1: 3,5 s" in book and "Conrad" in book


def test_a_page_that_was_read_passes(app, client, survey):
    setup(client)
    p, url = start(app, client)
    p.get(url)
    wait(app, 5)
    assert p.post(url, data={"nav": "next", "i2": "40"}).status_code == 302
    assert state(app) == (Response.COMPLETED, {"n": 1, "fast": 0, "hint": 0})
    row = export_row(client)
    assert (row["tempo_checked"], row["tempo_fast"], row["tempo_hint"], row["tempo_hint_at"]) == ("1", "0", "0", "")


def test_once_per_participation_but_counted_on(app, client, survey):
    setup(client, pages=2)
    p, url = start(app, client)
    p.get(url)
    assert "data-tempo-note" in p.post(url, data={"nav": "next", "i2": "1"}).data.decode()
    p.post(url, data={"nav": "next", "i2": "1"})
    p.get(url)
    assert p.post(url, data={"nav": "next", "i4": "1"}).status_code == 302  # fast again: counted, not shown
    assert state(app) == (Response.COMPLETED, {"n": 2, "fast": 2, "hint": 1, "at": "2"})


def test_every_time(app, client, survey):
    setup(client, pages=2, tempo_dose="always")
    p, url = start(app, client)
    for field in ("i2", "i4"):
        p.get(url)
        assert "data-tempo-note" in p.post(url, data={"nav": "next", field: "1"}).data.decode()
        p.post(url, data={"nav": "next", field: "1"})
    assert state(app) == (Response.COMPLETED, {"n": 2, "fast": 2, "hint": 2, "at": "2"})


def test_only_the_first_next_of_the_first_visit(app, client, survey):
    setup(client, pages=2, tempo_dose="always")
    p, url = start(app, client)
    p.get(url)
    wait(app, 10)
    p.post(url, data={"nav": "next", "i2": "1"})                     # page 1, read
    p.get(url)
    wait(app, 10)
    p.post(url, data={"nav": "back"})                                # "Back" is no answer: not checked
    p.get(url)
    assert p.post(url, data={"nav": "next", "i2": "1"}).status_code == 302  # page 1 again, at once: not checked
    p.get(url)
    # page 2: its first "Next" comes now and is checked (Mathe-Audit Q-11; it used to drop out of the count),
    # with the 10 s it was read before "Back": not too fast
    assert p.post(url, data={"nav": "next", "i4": "1"}).status_code == 302
    assert state(app) == (Response.COMPLETED, {"n": 2, "fast": 0, "hint": 0})


def test_a_page_left_with_back_is_checked_at_its_first_next(app, client, survey):
    """Mathe-Audit Q-11: a page whose first visit ends with "Back" is checked at its first "Next" later, with the
    time of all its visits; a page sent at once on both visits counts as too fast."""
    setup(client, pages=2, tempo_dose="always")
    p, url = start(app, client)
    p.get(url)
    wait(app, 10)
    p.post(url, data={"nav": "next", "i2": "1"})                     # page 1, read
    p.get(url)
    p.post(url, data={"nav": "back"})                                # page 2 left at once
    p.get(url)
    p.post(url, data={"nav": "next", "i2": "1"})
    p.get(url)
    assert "data-tempo-note" in p.post(url, data={"nav": "next", "i4": "1"}).data.decode()   # page 2, at once again
    status, kept = state(app)
    assert status == Response.ACTIVE and (kept["n"], kept["fast"], kept["hint"]) == (2, 1, 1)
    p.post(url, data={"nav": "next", "i4": "1"})                     # with the note seen: on, not checked again
    status, kept = state(app)
    assert status == Response.COMPLETED and (kept["n"], kept["fast"]) == (2, 1)


def test_only_in_chosen_blocks(app, client, survey):
    setup(client, tempo_hint="blocks")
    client.post("/surveys/1/editor/blocks")                          # block 3
    add(client, "text_input", block=3)                               # item 3
    client.post("/surveys/1/editor/items/3", data={"code": "q2", "question": TEN_WORDS})
    client.post("/surveys/1/editor/blocks/3", data={"title": "B", "tempo_hint": "1"})
    editor = client.get("/surveys/1/editor/").data.decode()
    assert "Tempo-Hinweis auf den Seiten dieses Blocks" in editor and "Tempo-Schwelle je Seite: 3,5 s" in editor
    with app.app_context():
        assert not (db.session.get(Block, 2).settings or {}).get("tempo_hint")
        assert db.session.get(Block, 3).settings["tempo_hint"] is True
    p, url = start(app, client)
    p.get(url)
    assert p.post(url, data={"nav": "next", "i2": "1"}).status_code == 302  # block 2: not chosen
    p.get(url)
    assert "data-tempo-note" in p.post(url, data={"nav": "next", "i3": "1"}).data.decode()
    assert state(app)[1] == {"n": 1, "fast": 1, "hint": 1, "at": "3"}


def test_pages_without_a_question_are_never_checked(app, client, survey):
    fill_consent(client)
    add(client, "info")                                              # item 2: a text only
    client.post("/surveys/1/editor/items/2", data={"code": "intro", "question": TEN_WORDS})
    client.post("/surveys/1/meta", data={**META, "tempo_hint": "all"})
    with app.app_context():
        assert tempo.block_thresholds(db.session.get(Survey, 1), db.session.get(Block, 2)) == [None]
    p, url = start(app, client)
    p.get(url)
    assert p.post(url, data={"nav": "next"}).status_code == 302
    assert state(app) == (Response.COMPLETED, None)


def test_note_comes_together_with_a_missing_answer(app, client, survey):
    setup(client)
    client.post("/surveys/1/editor/items/2", data={"code": "q1", "question": TEN_WORDS, "answer_mode": "required"})
    p, url = start(app, client)
    p.get(url)
    page = p.post(url, data={"nav": "next", "i2": ""}).data.decode()
    assert "data-tempo-note" in page and "has-error" in page         # one round for both
    page = p.post(url, data={"nav": "next", "i2": ""}).data.decode()
    assert "data-tempo-note" not in page and "has-error" in page
    assert p.post(url, data={"nav": "next", "i2": "7"}).status_code == 302


def test_dummy_data_are_never_checked(app, client, survey):
    setup(client)
    client.post("/surveys/1/dummy", data={"n": "3"})
    with app.app_context():
        dummies = db.session.scalars(db.select(Response).filter_by(source="dummy")).all()
        assert dummies and all("_tempo" not in (r.paradata or {}) for r in dummies)


def test_settings_are_locked_after_the_start_and_copied(app, client, survey):
    setup(client, tempo_dose="always")
    client.post("/surveys/1/duplicate", data={"title": "Kopie"})
    with app.app_context():
        copy = db.session.get(Survey, 2)
        assert copy is not None and (copy.tempo_hint, copy.tempo_dose, copy.page_timing) == ("all", "always", True)
    start(app, client)
    client.post("/surveys/1/meta", data={**META, "tempo_hint": "off"})
    with app.app_context():
        assert db.session.get(Survey, 1).tempo_hint == "all"
    assert "Tempo-Hinweis" in client.get("/surveys/1/quality").data.decode()


# --- later steps: own wording, pages without a question, choice tasks, the median rule, flow and quality -----


def later(app, seconds=9):
    """wait() for the participation that is under way (several may exist)."""
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter(Response.pid.is_not(None), Response.status == Response.ACTIVE)
                              .order_by(Response.id.desc()))
        shown = dict(r.paradata["_shown"])
        shown["at"] = (datetime.fromisoformat(shown["at"]) - timedelta(seconds=seconds)).isoformat()
        r.paradata = {**r.paradata, "_shown": shown}
        db.session.commit()


def test_own_wording_per_language(app, client, survey):
    setup(client)
    client.post("/surveys/1/meta", data={**META, "languages": ["de", "en"], "tempo_hint": "all", "tempo_options_shown": "1",
                                         "tempo_text_hint_de": "  Bitte   langsamer lesen. "})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert s.tempo_texts == {"de": {"hint": "Bitte langsamer lesen."}} and s.tempo_options is None
        assert tempo.message("de", s) == "Bitte langsamer lesen."
        assert "very quickly" in tempo.message("en", s)                # no own text in English: the standard one
        from app import translations

        unit = next(u for u in translations.units(s, "en") if u.key == "tempo")
        unit.set("en", {"hint": "Slower, please."})
        db.session.commit()
        assert tempo.message("en", db.session.get(Survey, 1)) == "Slower, please."
    assert "Bitte langsamer lesen." in client.get("/surveys/1/quality").data.decode()  # part "Datenqualität steigern"
    p, url = start(app, client)
    p.get(url)
    page = p.post(url, data={"nav": "next", "i2": "1"}).data.decode()
    assert "data-tempo-note" in page and "Bitte langsamer lesen." in page and "sehr schnell beantwortet" not in page
    # a form without the fields of a language keeps its wording; an emptied field goes back to the standard
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert tempo.texts_from_form({"tempo_text_hint_de": ""}, ["de", "en"], s.tempo_texts) == {"en": {"hint": "Slower, please."}}


def test_pages_without_a_question_can_be_checked(app, client, survey):
    fill_consent(client)
    add(client, "info")                                              # item 2: a text only
    client.post("/surveys/1/editor/items/2", data={"code": "intro", "question": TEN_WORDS})
    client.post("/surveys/1/meta", data={**META, "tempo_hint": "all", "tempo_options_shown": "1", "tempo_read": "1"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert tempo.options(s) == {"rule": "words", "read": True, "tasks": False}
        assert tempo.block_thresholds(s, db.session.get(Block, 2)) == [3.5]
    p, url = start(app, client)
    p.get(url)
    page = p.post(url, data={"nav": "next"}).data.decode()
    assert "data-tempo-note" in page and "sehr schnell weitergeklickt" in page   # a wording of its own
    assert state(app)[1] == {"n": 1, "fast": 1, "hint": 1, "at": "2"}
    assert p.post(url, data={"nav": "next"}).status_code == 302


def test_choice_tasks_can_be_checked(app, client, survey):
    from tests.analysis.test_maxdiff import maxdiff_form

    fill_consent(client)
    add(client, "maxdiff")                                           # item 2
    client.post("/surveys/1/editor/items/2", data=maxdiff_form())
    client.post("/surveys/1/editor/items/2/conjoint/design")
    client.post("/surveys/1/meta", data={**META, "tempo_hint": "all"})
    with app.app_context():
        s, item = db.session.get(Survey, 1), db.session.get(Item, 2)
        assert tempo.block_thresholds(s, db.session.get(Block, 2)) == [None]      # not without the tick
        sets = item.config["design"]["versions"][0]
        # the question (6 words) and the four statements of the set, one word each
        assert tempo.task_words(item, "de", 0, 0) == 10 and tempo.threshold([item], "de", spec={"task": 0}) == 3.5
        assert tempo.task_words(item, "de", 99, 0) is None
    client.post("/surveys/1/meta", data={**META, "tempo_hint": "all", "tempo_options_shown": "1", "tempo_tasks": "1"})
    with app.app_context():
        assert tempo.block_thresholds(db.session.get(Survey, 1), db.session.get(Block, 2)) == [3.5]
    p, url = start(app, client)
    p.get(url)
    answer = {"nav": "next", "i2_t0_b": str(sets[0][0]), "i2_t0_w": str(sets[0][1])}
    page = p.post(url, data=answer).data.decode()
    assert "data-tempo-note" in page and 'name="i2_t0_b"' in page       # the same set once more, with the note
    assert state(app) == (Response.ACTIVE, {"n": 1, "fast": 1, "hint": 1, "at": "2.t0"})
    assert p.post(url, data=answer).status_code == 302
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(is_preview=False))
        assert r.answers["2"]["t0"] == {"b": sets[0][0], "w": sets[0][1]} and r.position == 2
        from app import paradata

        names = {key: c[2:] for c, _, key in paradata.timing_columns(db.session.get(Survey, 1))}
        assert tempo.export_values(None, r, names)["tempo_hint_at"] == "md_t1"


def _times(app, key, seconds, n, source="real"):
    with app.app_context():
        for _ in range(n):
            db.session.add(Response(survey_id=1, source=source, is_preview=source == "test", paradata={"_t": {key: seconds}}))
        db.session.commit()


def test_median_rule_needs_enough_times_then_replaces_the_reading_time(app, client, survey):
    app.config["TEMPO_MEDIAN_SECONDS"] = 0                             # no keeping between the steps of this test
    setup(client)
    client.post("/surveys/1/meta", data={**META, "tempo_hint": "all", "tempo_options_shown": "1", "tempo_rule": "median"})
    _times(app, "2", 30.0, tempo.MEDIAN_MIN - 1, source="test")       # the pretest: stored test runs
    with app.app_context():
        s, items = db.session.get(Survey, 1), [db.session.get(Item, 2)]
        assert tempo.options(s)["rule"] == "median"
        assert tempo.median_limit(s, "2") is None and tempo.limit(s, items, "2", "de") == 3.5  # too few: reading time
    _times(app, "2", 30.0, 1, source="test")
    with app.app_context():
        s, items = db.session.get(Survey, 1), [db.session.get(Item, 2)]
        assert tempo.limit(s, items, "2", "de") == 15.0                # half of the median
    assert "Tempo-Schwelle je Seite: 15,0 s" in client.get("/surveys/1/editor/").data.decode()
    _times(app, "2", 8.0, 1)                                           # a real participation: the test runs no longer count
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert tempo.median_limit(s, "2") is None
        assert tempo.first_times({"_t": {"2": 9.0, "4": 5.0}, "_t1": {"2": 2.0}}) == {"2": 2.0, "4": 5.0}
    p, url = start(app, client)
    p.get(url)
    later(app, 5)                                                      # above the reading time of 3.5 s
    assert p.post(url, data={"nav": "next", "i2": "1"}).status_code == 302
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter(Response.pid.is_not(None), Response.status == Response.COMPLETED))
        assert 5 <= r.paradata["_t1"]["2"] < 7                         # the first visit is kept for the medians
    book = client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    assert "Hälfte der Median-Zeit" in book and "Greszki" in book


def test_medians_are_kept_per_process(app, client, survey):
    setup(client)
    _times(app, "2", 10.0, 3)
    with app.app_context():
        assert tempo.medians(db.session.get(Survey, 1)) == {"2": (10.0, 3)}
    _times(app, "2", 50.0, 9)
    with app.app_context():
        assert tempo.medians(db.session.get(Survey, 1)) == {"2": (10.0, 3)}     # still the kept ones
        app.config["TEMPO_MEDIAN_SECONDS"] = 0
        assert tempo.medians(db.session.get(Survey, 1)) == {"2": (50.0, 12)}


def _tempo_flow(app, client, value="1, 2"):
    """q1 | q2 | if pages were sent too fast: exclusion end | q3."""
    setup(client, pages=2, tempo_dose="always")
    client.post("/surveys/1/editor/blocks")                           # block 3
    add(client, "text_input", block=3)                                # item 5
    client.post("/surveys/1/editor/items/5", data={"code": "q3", "question": TEN_WORDS})
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.flow = {"t": "root", "children": [
            {"t": "block", "block": 1}, {"t": "block", "block": 2},
            {"t": "branch", "id": "b1", "cond": {"item": "tempo", "op": "in", "value": value},
             "children": [{"t": "end", "id": "e1", "text": {}, "kind": "excluded"}]},
            {"t": "block", "block": 3}]}
        db.session.commit()


def test_flow_condition_on_pages_sent_too_fast(app, client, survey):
    _tempo_flow(app, client, value="2")
    fast, url = start(app, client)
    for item in ("i2", "i4"):                                         # both pages too fast: note, then on
        fast.get(url)
        assert "data-tempo-note" in fast.post(url, data={"nav": "next", item: "1"}).data.decode()
        fast.post(url, data={"nav": "next", item: "1"})
    assert "Die Befragung endet für Sie an dieser Stelle" in fast.get(url, follow_redirects=True).data.decode()
    slow = app.test_client()                                          # one fast page only: passes the end
    slow.get(url)
    slow.post(url, data=CONSENT_OK)
    slow.get(url)
    slow.post(url, data={"nav": "next", "i2": "1"})
    slow.post(url, data={"nav": "next", "i2": "1"})
    slow.get(url)
    later(app)
    slow.post(url, data={"nav": "next", "i4": "1"})
    assert 'name="i5"' in slow.get(url).data.decode()
    with app.app_context():
        states = {r.status: r.paradata["_tempo"]["pos"] for r in db.session.scalars(db.select(Response).filter(Response.pid.is_not(None)))}
        assert states == {Response.EXCLUDED: [1, 2], Response.ACTIVE: [1]}


def test_flow_condition_counts_the_pages_before_and_reads_in_words(app, client, survey):
    from werkzeug.datastructures import MultiDict

    from app import conditions, design, flow

    _tempo_flow(app, client)
    assert tempo.at({"x": 1}, 3) == {"x": 1}                          # nothing asks for it: the view as it is
    view = {tempo.FAST_KEY: [2, 5, 7]}
    assert [tempo.at(view, n)[tempo.REF] for n in (2, 3, 6, 9)] == ["0", "1", "2", "2"]  # only the pages before
    with app.app_context():
        s = db.session.get(Survey, 1)
        described = conditions.describe(s, everything=True)
        assert [c for c, _ in described["tempo"]["options"]] == ["0", "1", "2"] and "tempo" not in conditions.describe(s)
        ticked = MultiDict([("item", "tempo"), ("mode", "is"), ("codes", "1"), ("codes", "2")])
        assert conditions.from_form(ticked, "", described) == {"item": "tempo", "op": "in", "value": "1, 2"}
        assert conditions.text(s, flow.conditions_by_id(s)["b1"]) == "Tempo: eine Seite zu schnell oder zwei oder mehr Seiten zu schnell"
        assert not any("Tempo-Hinweis ist aus" in f.message for f in design.check(s))
        assert tempo.flow_view(s, Response(paradata={"_tempo": {"pos": [1]}})) == {tempo.FAST_KEY: [1]}
        s.tempo_hint = "off"
        assert any("Tempo-Hinweis ist aus" in f.message for f in design.check(s))
        assert tempo.flow_view(s, Response(paradata={"_tempo": {"pos": [1]}})) == {}
        db.session.rollback()
    assert "Tempo" in client.get("/surveys/1/flow/").data.decode()


def test_a_page_stays_on_the_path_when_it_or_a_later_page_is_fast(app, client, survey):
    """q1 is shown to those without a fast page before it. Being fast on q1 itself, or later, must neither drop
    its answer as skipped nor take the page off the person's path (quality control)."""
    from app import export, quality

    setup(client, pages=1, tempo_dose="always")
    client.post("/surveys/1/editor/blocks")                           # block 3
    add(client, "text_input", block=3)                                # item 3
    client.post("/surveys/1/editor/items/3", data={"code": "q2", "question": TEN_WORDS})
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.flow = {"t": "root", "children": [
            {"t": "block", "block": 1},
            {"t": "branch", "id": "b1", "cond": {"item": "tempo", "op": "eq", "value": "0"},
             "children": [{"t": "block", "block": 2}]},
            {"t": "block", "block": 3}]}
        db.session.commit()
    p, url = start(app, client)
    p.get(url)
    assert "data-tempo-note" in p.post(url, data={"nav": "next", "i2": "7"}).data.decode()
    assert p.post(url, data={"nav": "next", "i2": "8"}).status_code == 302   # the second "Next" still belongs to q1
    p.get(url)
    p.post(url, data={"nav": "next", "i3": "1"})
    p.post(url, data={"nav": "next", "i3": "1"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        r = db.session.scalar(db.select(Response).filter(Response.pid.is_not(None)))
        assert r.status == Response.COMPLETED and r.answers["2"] == "8" and r.answers["3"] == "1"
        ctx = quality.context(s, export._items(s))
        assert [item.code for item, _ in quality.shown(s, r, ctx)] == ["q1", "q2"]


def test_share_of_fast_pages_is_a_quality_indicator_and_can_mark(app, client, survey):
    from app import export, quality

    setup(client, pages=2)
    p, url = start(app, client)
    p.get(url)
    p.post(url, data={"nav": "next", "i2": "1"})
    p.post(url, data={"nav": "next", "i2": "1"})
    p.get(url)
    later(app)
    p.post(url, data={"nav": "next", "i4": "1"})
    row = export_row(client)
    assert (row["tempo_checked"], row["tempo_fast"], row["qc_tempo"]) == ("2", "1", "0.5")
    assert row["qc_flags"] == "1" and row["qc_marked"] == "0"         # one conspicuous thing alone marks nobody
    client.post("/surveys/1/quality", data={"source": "real", "qc_flag": "tempo", "qc_min": "1"})
    assert export_row(client)["qc_marked"] == "1"
    page = client.get("/surveys/1/quality").data.decode()
    assert "Seiten zu schnell abgeschickt" in page and "qc_tempo" in page
    book = client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    assert "qc_tempo" in book and "tempo_fast geteilt durch tempo_checked" in book
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.tempo_hint = "off"                                           # without the hint: no column, no flag to tick
        assert "qc_tempo" not in [c for c, _ in quality.columns([], False)]
        assert "tempo" not in [k for k, *_ in quality.available(quality.context(s, export._items(s)))]
        db.session.rollback()


def test_tempo_per_page_on_the_quality_page(app, client, survey):
    setup(client, pages=2)
    client.post("/surveys/1/editor/items/2", data={"code": "q1", "question": TEN_WORDS + " " + TEN_WORDS})  # 7 s
    with app.app_context():
        for seconds in (2.0, 6.0, 10.0, 30.0):
            db.session.add(Response(survey_id=1, source="real", paradata={"_t": {"2": seconds + 50}, "_t1": {"2": seconds}}))
        db.session.commit()
        rows = tempo.calibration(db.session.get(Survey, 1))
        assert [(r["name"], r["kind"], r["on"], r["n"]) for r in rows] == [("q1", "Fragen", True, 4), ("q2", "Fragen", True, 0)]
        first = rows[0]
        assert (first["median"], first["words"], first["below_words"], first["half"], first["below_half"]) == (8.0, 7.0, 0.5, 4.0, 0.25)
        assert rows[1]["median"] is None and rows[1]["words"] == 3.5 and rows[1]["below_words"] is None
    page = client.get("/surveys/1/quality").data.decode()
    assert "Tempo je Seite" in page and "<td>50 %</td>" in page and "<td>25 %</td>" in page and "Greszki" in page
    client.post("/surveys/1/meta", data={**META, "page_timing": "1", "tempo_hint": "off"})  # page times alone are enough
    page = client.get("/surveys/1/quality").data.decode()
    assert "Tempo je Seite" in page and "der Tempo-Hinweis ist aus" in page


def test_later_settings_are_copied_and_travel_in_the_file(app, client, survey):
    import json

    setup(client)
    client.post("/surveys/1/meta", data={**META, "tempo_hint": "all", "tempo_options_shown": "1", "tempo_rule": "median",
                                         "tempo_read": "1", "tempo_text_hint_read_de": "Bitte lesen."})
    wanted = ({"rule": "median", "read": True, "tasks": False}, {"de": {"hint_read": "Bitte lesen."}})
    client.post("/surveys/1/duplicate", data={"title": "Kopie"})
    with app.app_context():
        copy = db.session.get(Survey, 2)
        assert (copy.tempo_options, copy.tempo_texts) == wanted
    body = client.get("/surveys/1/export/survey.json")
    if body.status_code == 200:
        data = json.loads(body.data)
        assert (data["survey"]["tempo_options"], data["survey"]["tempo_texts"]) == wanted
    with app.app_context():
        from app import transfer

        read = transfer._settings({"title": "T", "languages": ["de"], "tempo_hint": "all",
                                   "tempo_options": {"rule": "x", "read": "yes", "tasks": True},
                                   "tempo_texts": {"de": {"hint": " A  b ", "other": "x"}, "xx": {"hint": "y"}, "en": 3}}, "")
        assert read["tempo_options"] == {"rule": "words", "read": False, "tasks": True}
        assert read["tempo_texts"] == {"de": {"hint": "A b"}}
