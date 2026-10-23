"""Package P1 of the security and performance audit of 2026-10-09 ("Schnelle Performance"), without changing what
anyone sees:

N-1  item_texts._keys keeps the keys in a dict: linear instead of quadratic in the number of options
N-2  Item.resolved is kept on the object while its stored texts stay the same objects
N-3  a participant page keeps what it loaded over its commit, so the questionnaire is not loaded twice
N-7  ix_response_start_flood answers survey_start_flood without reading the rows
"""
import random
import time

from sqlalchemy import event

from app import item_texts, limits
from app.extensions import db
from app.models import Item, Response, Survey
from tests.conftest import CONSENT_OK, add, experiments_on, fill_consent


# --- N-1 -------------------------------------------------------------------------------------------------------


def _keys_before(*dicts):
    out = []
    for d in dicts:
        for k in d:
            if k not in out:
                out.append(k)
    return out


def test_keys_are_the_same_in_the_same_order():
    rng = random.Random(9)
    for _ in range(200):
        dicts = [{f"k{rng.randrange(40)}": None for _ in range(rng.randrange(30))} for _ in range(rng.randrange(1, 5))]
        assert item_texts._keys(*dicts) == _keys_before(*dicts)
    assert item_texts._keys() == [] and item_texts._keys({}, {"a": 1}, {"b": 1, "a": 2}) == ["a", "b"]


def test_keys_of_many_options_take_no_square_time():
    big = {f"o{n}": "x" for n in range(20000)}
    start = time.process_time()
    assert len(item_texts._keys(big, dict(big), {"extra": 1})) == 20001
    assert time.process_time() - start < 0.5  # the list took minutes for this


# --- N-2 -------------------------------------------------------------------------------------------------------


def _choice(app, client):
    add(client, "single_choice")
    with app.app_context():
        item = db.session.get(Item, 2)
        item.texts = {"de": {"question": "Frage?", "options": {"a": "Eins", "b": "Zwei"}},
                      "en": {"question": "Question?"}}
        db.session.commit()


def test_resolved_texts_are_kept_while_the_texts_stay(app, client, survey):
    _choice(app, client)
    with app.app_context():
        item = db.session.get(Item, 2)
        first = item.resolved("de")
        assert item.resolved("de") is first and item.resolved() is first  # no lang: the default language
        assert item.t("en")["question"] == "Question?" and item.resolved("en") is not first
        assert item.t("en")["options"] == {"a": "Eins", "b": "Zwei"}  # falls back to the default language
        assert item.text_lang("options", "en", "a") == "de"


def test_a_change_of_the_texts_resolves_anew(app, client, survey):
    _choice(app, client)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.text("question", "de") == "Frage?"
        item.texts = {**item.texts, "de": {**item.texts["de"], "question": "Neu?"}}  # how the code changes texts
        assert item.text("question", "de") == "Neu?" and item.text("question", "en") == "Question?"
        item.texts["en"] = {"question": "New?", "options": {"a": "One"}}  # a language replaced in place
        assert item.text("question", "en") == "New?" and item.t("en")["options"] == {"a": "One", "b": "Zwei"}
        db.session.commit()  # loaded again after the commit: a new object, resolved anew
        assert item.text("question", "en") == "New?"
    with app.app_context():
        assert db.session.get(Item, 2).t("en")["options"] == {"a": "One", "b": "Zwei"}


def test_the_editor_shows_what_it_just_stored(app, client, survey):
    _choice(app, client)
    client.post("/surveys/1/editor/items/2", data={"code": "q", "question": "Gespeichert?"})
    assert "Gespeichert?" in client.get("/surveys/1/editor/items/2").data.decode()
    with app.app_context():
        survey_ = db.session.get(Survey, 1)
        item = db.session.get(Item, 2)
        assert item.text("question", "de") == "Gespeichert?"
        survey_.default_language = "en"  # another default language: resolved anew, not taken from the memo
        assert item.text("question") == "Question?"
        db.session.rollback()


# --- N-3 -------------------------------------------------------------------------------------------------------


def _running(app, client, *types):
    fill_consent(client)
    for t in types:
        add(client, t)
    client.post("/surveys/1/editor/items/2", data={"code": "why", "question": "Warum?", "answer_mode": "optional"})
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        survey = db.session.get(Survey, 1)
        assert survey.status == Survey.RUNNING
        return survey.code


def _item_loads(app, fn):
    seen = []
    with app.app_context():
        engine = db.engine

    def note(conn, cursor, statement, *_):
        # the items of a block (Block.items), not an item of the page read again by its id after the commit
        if statement.lstrip().upper().startswith("SELECT") and "FROM item" in statement and "= item.block_id" in statement:
            seen.append(statement)

    event.listen(engine, "before_cursor_execute", note)
    try:
        result = fn()
    finally:
        event.remove(engine, "before_cursor_execute", note)
    return result, len(seen)


def test_a_participant_page_loads_the_items_of_a_block_once(app, client, survey):
    code = _running(app, client, "text_input")
    p = app.test_client()
    p.get(f"/s/{code}")
    assert p.post(f"/s/{code}", data=CONSENT_OK).status_code == 302
    with app.app_context():
        blocks = len(db.session.get(Survey, 1).blocks)
    page, loads = _item_loads(app, lambda: p.get(f"/s/{code}"))
    assert page.status_code == 200 and 'name="i2"' in page.data.decode()
    assert loads <= blocks  # before: every block once more after the commit
    assert "microphone=()" in page.headers["Permissions-Policy"]
    assert "geolocation=()" in page.headers["Permissions-Policy"]


def test_microphone_and_position_are_still_allowed_where_used(app, client, survey):
    experiments_on(client, "voice", "geolocation")
    code = _running(app, client, "text_input", "voice", "geolocation")
    with app.app_context():
        assert {i.type for b in db.session.get(Survey, 1).blocks for i in b.items} >= {"voice", "geolocation"}
    p = app.test_client()
    p.get(f"/s/{code}")
    assert p.post(f"/s/{code}", data=CONSENT_OK).status_code == 302
    policy = p.get(f"/s/{code}").headers["Permissions-Policy"]
    assert "microphone=(self)" in policy and "geolocation=(self)" in policy


# --- N-7 -------------------------------------------------------------------------------------------------------


def test_the_start_limit_reads_only_the_index(app):
    with app.app_context():
        assert any(ix.name == "ix_response_start_flood" for ix in Response.__table__.indexes)
        if db.engine.dialect.name != "sqlite":
            return
        sql = ("EXPLAIN QUERY PLAN SELECT started_at FROM response WHERE survey_id = 1 AND source = 'real' "
               "ORDER BY id DESC LIMIT 1 OFFSET 2999")
        plan = " ".join(str(row[-1]) for row in db.session.execute(db.text(sql)))
        assert "COVERING INDEX ix_response_start_flood" in plan


def test_the_start_limit_still_counts(app, client, survey):
    code = _running(app, client, "text_input")
    app.config.update(SURVEY_START_LIMIT=2, SURVEY_START_WINDOW=600)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert not limits.survey_start_flood(s)
    for n in range(2):
        p = app.test_client()
        p.get(f"/s/{code}", environ_base={"REMOTE_ADDR": f"198.51.100.{n + 1}"})
        p.post(f"/s/{code}", data=CONSENT_OK, environ_base={"REMOTE_ADDR": f"198.51.100.{n + 1}"})
    with app.app_context():
        assert limits.survey_start_flood(db.session.get(Survey, 1))
