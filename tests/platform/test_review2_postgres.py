"""Code review 2026-10-03, package PostgreSQL (PG-1 to PG-11, R-7). Every test runs on SQLite and, with
TEST_DATABASE_URL, on PostgreSQL; the ones about PostgreSQL's own behaviour (locks, system columns) only there."""
import json

import pytest
from sqlalchemy import Boolean, Column, Integer, String, inspect, text
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app import quotas, tables
from app.extensions import add_missing_columns, db
from app.models import Asset, Feedback, Item, PreviewComment, QuotaCell, Response, Survey, User
from tests.conftest import PG_URL, add
from tests.quotas.test_quota_field import MEN, idle
from tests.quotas.test_quotas import build, screen, start

needs_pg = pytest.mark.skipif(not PG_URL, reason="about PostgreSQL itself (TEST_DATABASE_URL)")


# --- PG-1 / PG-5: control characters in form values ------------------------------------------------------


def test_control_characters_are_removed_from_form_query_and_json_values(app):
    with app.test_request_context("/?q=a%00b", method="POST", data={"t": "x\x00y\r\n\tz\x07", "k\x00": "1"}):
        from flask import request

        assert request.args["q"] == "ab"
        assert request.form["t"] == "xy\r\n\tz" and request.form["k"] == "1"  # line breaks and tabs stay
    body = json.dumps({"a\x00": ["b\x00c", {"d": "e\x1f"}], "n": 1})
    with app.test_request_context("/", method="POST", data=body, content_type="application/json"):
        from flask import request

        assert request.get_json() == {"a": ["bc", {"d": "e"}], "n": 1}


def test_a_nul_in_a_free_text_gives_no_error(app, client, survey):
    r = client.post("/feedback/new", data={"message": "Fehler\x00 hier", "severity": "1"})
    assert r.status_code == 302
    with app.app_context():
        assert db.session.scalar(db.select(Feedback.message)) == "Fehler hier"


def test_a_stored_nul_does_not_break_the_check_for_real_answers(app, client, survey):
    add(client, "single_choice")                                                            # 2
    add(client, "text_input")                                                               # 3
    with app.app_context():
        db.session.add(Response(survey_id=1, answers={"3": "a\x00b"}, layout={"cj": {"9": "\x00"}}, source="real",
                                is_preview=False))
        db.session.commit()
        choice, typed = db.session.get(Item, 2), db.session.get(Item, 3)
        assert typed.kind.has_real_answers(typed) is True
        assert choice.kind.has_real_answers(choice) is False
        assert db.session.scalar(db.select(db.func.count()).select_from(Response)) == 1     # the session goes on
    assert client.get("/surveys/1/editor/").status_code == 200


# --- PG-2: recounting locks the cells --------------------------------------------------------------------


@needs_pg
def test_recount_locks_the_cells_until_it_is_stored(app, client, survey):
    build(app, client, cells=MEN)
    start(app, client)
    with app.app_context():
        quotas.recount(db.session.get(Survey, 1))                                           # not committed yet
        cell = db.session.scalar(db.select(QuotaCell.id))
        with db.engine.connect() as other:                                                  # another server process
            other.execute(text("SET lock_timeout = '300ms'"))
            with pytest.raises(Exception, match="lock"):
                other.execute(text("UPDATE quota_cell SET running = running + 1 WHERE id = :id"), {"id": cell})
            other.rollback()
        db.session.commit()


# --- PG-3: the separated section in PostgreSQL ------------------------------------------------------------
# (tests/quotas/test_split.py, test_entries_share_one_transaction_in_key_order)


# --- PG-4: long keys of generated images -----------------------------------------------------------------


def test_long_image_keys_are_hashed_and_still_found(app, client, survey, monkeypatch):
    from app import loops
    from app.items.image import KEY_LENGTH, asset_key

    short = asset_key({"farbe": "rot"}, {"farbe"})
    assert short == '{"farbe": "rot"}'                                                      # existing keys stay valid
    long_row = {"beschreibung": "x" * 495, "farbe": "blau"}
    key = asset_key(long_row, {"beschreibung"})
    assert key.startswith("h:") and len(key) <= KEY_LENGTH and key == asset_key(dict(long_row), {"beschreibung"})
    add(client, "image")                                                                    # 2
    with app.app_context():
        item = db.session.get(Item, 2)
        item.config = {**item.config, "source": "ai", "prompt": "Ein Bild von {{beschreibung}}"}
        monkeypatch.setattr(loops, "active", lambda block: True)
        monkeypatch.setattr(loops, "all_variables", lambda block: [{"beschreibung": "kurz"}, long_row])
        assert key in item.kind.required_keys(item)
        assert item.kind.key_label(item, key) == "beschreibung: " + "x" * 495
        assert "x" * 495 in item.kind.prompt_for_key(item, key)
        db.session.add(Asset(item_id=2, key=key, filename="a.png", source="ai", status=Asset.APPROVED))
        db.session.commit()                                                                 # PostgreSQL refused 509 characters
        assert item.kind.asset_for(item, long_row).key == key


# --- PG-6 and PG-7: values longer than their column ------------------------------------------------------


def test_a_sample_size_beyond_the_column_is_refused(app, client, survey):
    r = client.post("/surveys/1/start", data={"use_max": "1", "max_responses": str(2 ** 31)}, follow_redirects=True)
    assert r.status_code == 200 and "höchstens 10.000.000" in r.data.decode()
    with app.app_context():
        assert db.session.get(Survey, 1).status == Survey.DRAFT


def test_long_names_are_cut_to_their_column(app, client, survey):
    from app import comments

    other = app.test_client()
    assert other.get("/auth/dev-login?orcid=0000-0002-1825-0097&name=" + "N" * 300).status_code == 302
    with app.app_context():
        user = db.session.scalar(db.select(User).filter_by(orcid="0000-0002-1825-0097"))
        assert user.name == "N" * 200
        comment = PreviewComment(survey_id=1, block_id=2, text="Tippfehler", author="A")
        db.session.add(comment)
        db.session.commit()
        comments.handle(comment, "resolved", user)
        assert db.session.get(PreviewComment, comment.id).handled_by == "N" * 120


# --- PG-8: search folds the same letters on both databases -----------------------------------------------


def test_search_folds_accents_as_text_key_does(app):
    assert all(tables.text_key(a) == p for a, p in zip(tables._ACCENTED, tables._PLAIN))
    assert {"ğ", "ș", "Ğ", "Ș", "ễ"} <= set(tables._ACCENTED)
    with app.app_context():
        folded = db.session.scalar(db.select(tables.key(db.literal("  Doğan   ȘERBAN Nguyễn Øre "))))
        assert folded == tables.text_key("  Doğan   ȘERBAN Nguyễn Øre ") == "dogan serban nguyen øre"


# --- PG-9: a fixed order -------------------------------------------------------------------------------


def test_the_responses_of_a_survey_come_in_order(app, client, survey):
    with app.app_context():
        for n in (7, 3, 5):
            db.session.add(Response(id=n, survey_id=1, answers={}, source="real", is_preview=False))
        db.session.commit()
        db.session.expire_all()
        assert [r.id for r in db.session.get(Survey, 1).responses] == [3, 5, 7]


# --- PG-10 and PG-11: the start-up steps ----------------------------------------------------------------


@needs_pg
def test_the_start_up_steps_run_one_process_at_a_time(app):
    from app.extensions import START_UP_LOCK, start_up_lock

    with app.app_context():
        with db.engine.connect() as other:
            try_lock = text("SELECT pg_try_advisory_lock(:key)")
            with start_up_lock():
                assert other.execute(try_lock, {"key": START_UP_LOCK}).scalar() is False
            assert other.execute(try_lock, {"key": START_UP_LOCK}).scalar() is True
            other.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": START_UP_LOCK})
            other.commit()


def test_missing_columns_are_added_on_either_database(app, caplog):
    table = db.metadata.tables["feedback"]
    added = [Column("x_flag", Boolean, nullable=False, default=True), Column("x_int", Integer, nullable=False, default=True),
             Column("x_text", String(10), nullable=False, default="o'k"), Column("x_null", String(5)),
             Column("x_computed", String(10), nullable=False, default=lambda: "x")]
    try:
        for column in added:
            table.append_column(column)
        with app.app_context():
            add_missing_columns(app)
            add_missing_columns(app)                                                        # a second start changes nothing
            columns = {c["name"]: c for c in inspect(db.engine).get_columns("feedback")}
            assert {"x_flag", "x_int", "x_text", "x_null"} <= set(columns) and "x_computed" not in columns
            assert not columns["x_flag"]["nullable"] and columns["x_null"]["nullable"]
            assert "true" in str(columns["x_flag"]["default"]).lower() or columns["x_flag"]["default"] == "1"
            assert "o''k" in str(columns["x_text"]["default"])
            db.session.execute(text("INSERT INTO feedback (message, page, severity, created_at, status) "
                                    "VALUES ('m', '/', 1, CURRENT_TIMESTAMP, 'open')"))
            row = db.session.execute(text("SELECT x_flag, x_int, x_text, x_null FROM feedback")).one()
            assert (bool(row[0]), row[1], row[2], row[3]) == (True, 1, "o'k", None)
            db.session.rollback()
        assert "feedback.x_computed is missing" in caplog.text
    finally:
        for column in added:
            table._columns.remove(column)


# --- R-7: giving back a reservation raises the version counter --------------------------------------------


def test_releasing_a_reservation_turns_away_an_older_state(app, client, survey):
    build(app, client, cells=MEN)
    uid = start(app, client)
    screen(app, uid)
    idle(app, 120)
    with app.app_context():
        with Session(db.engine) as other:                                                   # a request that read it before
            stale = other.get(Response, 1)
            before = stale.version
            assert quotas.release_expired() == 1
            db.session.commit()
            assert db.session.get(Response, 1).version == before + 1
            stale.quota = {**stale.quota}
            stale.last_active_at = None
            with pytest.raises(StaleDataError):
                other.commit()                                                              # would take the place off again
    assert quotas_running(app) == 0


def quotas_running(app):
    with app.app_context():
        return db.session.scalar(db.select(QuotaCell.running))
