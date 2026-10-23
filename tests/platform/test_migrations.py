"""Database migrations (app/migrations.py): the migrations build the schema of the models, a database from before
them is compared and stamped, one that differs keeps the app from starting, every migration goes down and up again,
SQLite gets a copy before a pending migration. Runs on PostgreSQL too (TEST_DATABASE_URL)."""
import os
import sqlite3

import pytest
from alembic import command
from sqlalchemy import MetaData, create_engine
from sqlalchemy import inspect as sqlalchemy_inspect

from app import migrations, models  # noqa: F401; metadata tests must not depend on a preceding app fixture
from app.extensions import db
from tests.conftest import PG_URL, _config, _pg_admin, _pg_name, _pg_url, needs_sqlite


# SQLite's batch mode copies and drops referenced tables, and these tests build old schemas with rows a later step
# repairs; PostgreSQL alters in place. They run without PRAGMA foreign_keys (tests/conftest.py, audit 2026-10-21, B8).
NO_FK = pytest.mark.sqlite_foreign_keys_off


def _fresh_url(tmp_path, kind):
    """An empty database: a SQLite file in tmp_path, or a new PostgreSQL database."""
    if not PG_URL:
        return None, f"sqlite:///{tmp_path / 'test.db'}"
    name = _pg_name(kind)
    _pg_admin(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)", f"CREATE DATABASE {name}")
    url = _pg_url(name)
    return url, url


def _old_schema(url, without_fk=None):
    """The tables as the code before the migrations made them: create_all with the models, but without the naming
    convention (PostgreSQL then names constraints itself, SQLite leaves them unnamed). without_fk: "table.column"
    whose foreign key is left out (as add_missing_columns added such columns)."""
    old = MetaData()
    for table in db.metadata.sorted_tables:
        table.to_metadata(old)
    if without_fk:
        name, column = without_fk.split(".")
        table = old.tables[name]
        for fk in [c for c in table.foreign_key_constraints if column in c.column_keys]:
            table.constraints.discard(fk)
            table.c[column].foreign_keys.clear()
    engine = create_engine(url)
    old.create_all(engine)
    engine.dispose()


def _dispose(app):
    with app.app_context():
        db.engine.dispose()
        db.engines.clear()


def test_migrations_build_the_schema_of_the_models(app, client):
    with app.app_context():
        assert migrations.current() == migrations.head()
        with db.engine.connect() as connection:
            assert migrations.differences(connection) == []
    text = client.get("/admin/database").get_data(as_text=True)
    assert f"Stand <code>{migrations.head()}</code>, aktuell" in text and "Migrationen (" in text


def test_a_database_from_before_the_migrations_is_compared_and_stamped(tmp_path):
    """Both servers in October 2026: tables made by create_all, no alembic_version. On PostgreSQL this also shows
    that the naming convention gives the names PostgreSQL chose itself."""
    from app import create_app

    url, address = _fresh_url(tmp_path, "old")
    _old_schema(address)
    app = create_app(_config(tmp_path, url))
    with app.app_context():
        assert migrations.current() == migrations.head()
        with db.engine.connect() as connection:
            assert migrations.differences(connection) == []
    _dispose(app)
    again = create_app(_config(tmp_path, url))  # the next start: nothing to do
    _dispose(again)


def test_a_database_without_a_needed_column_type_keeps_the_app_from_starting(tmp_path, caplog):
    from sqlalchemy import String

    from app import create_app

    url, address = _fresh_url(tmp_path, "odd")
    column = db.metadata.tables["feedback"].c.message
    kept = column.type
    try:
        column.type = String(5)  # an old schema where the column had another type
        _old_schema(address)
    finally:
        column.type = kept
    with pytest.raises(migrations.SchemaMismatch, match="feedback.message"):
        create_app(_config(tmp_path, url))
    assert "not stamped" in caplog.text
    engine = create_engine(address)
    with engine.connect() as connection:
        assert migrations.current(connection) is None
    engine.dispose()


@needs_sqlite
def test_what_already_runs_is_stamped_and_listed(tmp_path, caplog):
    """A column add_missing_columns added once has no foreign key, an old manual change left a column: both run
    today, so the database is stamped and the page names them."""
    from app import create_app

    _, address = _fresh_url(tmp_path, "drift")
    _old_schema(address, without_fk="feedback.survey_id")
    con = sqlite3.connect(tmp_path / "test.db")
    con.execute('ALTER TABLE feedback ADD COLUMN "handmade" TEXT')
    con.commit()
    con.close()
    app = create_app(_config(tmp_path))
    with app.app_context():
        assert migrations.current() == migrations.head()
    assert "left as they are" in caplog.text and "feedback.handmade" in caplog.text
    c = app.test_client()
    c.get("/auth/dev-login?orcid=0000-0000-0000-001X&name=Philipp")
    c.post("/terms/", data={"accept": "1"})
    text = c.get("/admin/database").get_data(as_text=True)
    assert "Abweichungen vom Schema des Codes" in text and "feedback.handmade" in text and "add_fk" in text
    _dispose(app)


@NO_FK
def test_every_migration_goes_down_and_up_again(app):
    with app.app_context():
        migrations._run(command.downgrade, "base")
        with db.engine.connect() as connection:
            assert migrations.current(connection) is None
        migrations._run(command.upgrade)
        assert migrations.current() == migrations.head()
        with db.engine.connect() as connection:
            assert migrations.differences(connection) == []


@needs_sqlite
def test_sqlite_gets_a_copy_before_a_migration(app):
    with app.app_context():
        path = migrations.backup(app)
    assert path and os.path.dirname(path).endswith("backups") and path.endswith("-vor-migration.db")
    con = sqlite3.connect(path)
    assert ("survey",) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    con.close()


def test_new_columns_need_a_migration():
    """The models and the migrations must say the same: a column added to a model without a migration file makes
    the comparison find it (what tests/platform/test_migrations.py relies on in every run)."""
    from sqlalchemy import Column, String

    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        migrations._run_on(connection, command.upgrade)
        table = db.metadata.tables["feedback"]
        extra = Column("not_migrated", String(10))
        table.append_column(extra)
        try:
            assert ("add_column", "feedback.not_migrated") in migrations.differences(connection)
        finally:
            table._columns.remove(extra)
        assert migrations.differences(connection) == []


@NO_FK
def test_0002_adds_the_missing_keys_of_preview_comment(tmp_path):
    """A database stamped 0001 whose preview_comment lacks the two foreign keys (add_missing_columns added the
    columns, Ralf's server): 0002 adds them, a reference to a gone account becomes NULL first."""
    from app import create_app

    url, address = _fresh_url(tmp_path, "keys")
    _old_schema(address, without_fk="preview_comment.author_id")
    engine = create_engine(address)
    with engine.connect() as connection:
        _drop_fk_if_any(connection, "preview_comment", "handled_by_id")
        migrations._run_on(connection, command.stamp, "0001")
        if not PG_URL:  # a comment of an account that is gone (SQLite checks no keys, so the row can exist)
            connection.exec_driver_sql("INSERT INTO preview_comment (survey_id, target, qversion, text, author, author_id, "
                                       "via, status, handled_by, note, created_at) VALUES (1, '', 1, 'x', 'x', 999, "
                                       "'editor', 'open', '', '', CURRENT_TIMESTAMP)")
        connection.commit()
    engine.dispose()
    app = create_app(_config(tmp_path, url))
    with app.app_context():
        assert migrations.current() == migrations.head()
        with db.engine.connect() as connection:
            assert not [d for d in migrations.differences(connection) if "preview_comment" in d[1]]
            if not PG_URL:
                assert connection.exec_driver_sql("SELECT author_id FROM preview_comment").scalar() is None
    _dispose(app)


def _drop_fk_if_any(connection, table, column):
    """PostgreSQL only: drop the foreign key of `column` (SQLite cannot; there _old_schema leaves one out)."""
    from sqlalchemy import inspect

    if connection.dialect.name != "postgresql":
        return
    for fk in inspect(connection).get_foreign_keys(table):
        if fk["constrained_columns"] == [column]:
            connection.exec_driver_sql(f'ALTER TABLE {table} DROP CONSTRAINT "{fk["name"]}"')



@NO_FK
def test_0014_brings_keys_and_required_columns_in_line(tmp_path):
    """A database stamped before 0014 with the differences of an old server (2026-10-06): keys add_missing_columns
    never made, keys of survey_label without ON DELETE, survey.code and response.answers that may be empty. 0014
    leaves no difference; a reference to a gone survey becomes NULL, an empty code gets one, empty answers {}."""
    from app import create_app, models  # noqa: F401  (the tables of the models, also when this test runs alone)

    url, address = _fresh_url(tmp_path, "keys14")
    old = MetaData()
    for table in db.metadata.sorted_tables:
        table.to_metadata(old)
    for name, column in (("feedback", "survey_id"), ("preview_comment", "author_id"), ("preview_comment", "handled_by_id")):
        table = old.tables[name]
        for fk in [c for c in table.foreign_key_constraints if column in c.column_keys]:
            table.constraints.discard(fk)
            table.c[column].foreign_keys.clear()
    for fk in old.tables["survey_label"].foreign_key_constraints:
        fk.ondelete, fk.name = None, None  # as SQLite made them before the naming convention
    old.tables["survey"].c.code.nullable = True
    old.tables["response"].c.answers.nullable = True
    engine = create_engine(address)
    old.create_all(engine)
    with engine.connect() as connection:
        migrations._run_on(connection, command.stamp, "0013")
        before = [d for d in migrations.differences(connection)]
        assert {k for k, _ in before} >= {"add_fk", "remove_fk", "modify_nullable"}
        assert "survey_label.survey_id -> survey.id" in " ".join(w for _, w in before) or PG_URL
        tables = db.metadata.tables  # inserts with the defaults of the models
        connection.execute(tables["user"].insert().values(id=1, orcid="0000-0001"))
        connection.execute(tables["survey"].insert().values(id=1, title="S", created_by_id=1, code=None))
        connection.execute(tables["feedback"].insert().values(message="x", survey_id=999))
        connection.commit()
    engine.dispose()
    app = create_app(_config(tmp_path, url))
    with app.app_context():
        assert migrations.current() == migrations.head()
        with db.engine.connect() as connection:
            assert migrations.differences(connection) == []
            assert connection.exec_driver_sql("SELECT survey_id FROM feedback").scalar() is None
            assert connection.exec_driver_sql("SELECT code FROM survey WHERE id = 1").scalar()
    _dispose(app)



def _unstamped_without_answer_keys(folder, duplicates=False):
    from app import create_app
    from app.models import Response, Survey
    from tests.conftest import _config

    first = create_app(_config(folder))
    with first.app_context():
        with db.engine.begin() as conn:
            conn.exec_driver_sql("DROP INDEX ix_response_uuid")
            conn.exec_driver_sql("DROP INDEX ix_response_prompt")
            conn.exec_driver_sql("DROP TABLE alembic_version")
        if duplicates:
            s = Survey(title="legacy")
            db.session.add(s)
            db.session.flush()
            db.session.add_all([Response(survey_id=s.id, enrolment_id=1, uuid="same-id", answers={"a": i})
                                for i in range(2)])
            db.session.commit()
        db.engine.dispose()
        db.engines.clear()


def test_stamping_creates_missing_unique_indexes(tmp_path):
    """Audit 2026-10-17, S-R1: a database made before the migrations got stamped without the unique indexes of the
    diary answers, so the same answer id could be stored twice. They are created before the stamp."""
    from sqlalchemy import inspect

    from app import create_app
    from app.models import Response, Survey
    from tests.conftest import _config

    folder = tmp_path / "legacy"
    folder.mkdir()
    _unstamped_without_answer_keys(folder)
    again = create_app(_config(folder))
    with again.app_context():
        assert migrations.current() == migrations.head()
        indexes = {i["name"] for i in inspect(db.engine).get_indexes("response")}
        assert {"ix_response_uuid", "ix_response_prompt"} <= indexes
        s = Survey(title="legacy")
        db.session.add(s)
        db.session.flush()
        db.session.add_all([Response(survey_id=s.id, enrolment_id=1, uuid="same-id", answers={"a": i}) for i in range(2)])
        with pytest.raises(Exception):
            db.session.commit()
        db.session.rollback()
        db.engine.dispose()
        db.engines.clear()


def test_stamping_stops_on_rows_that_break_a_unique_index(tmp_path):
    """S-R1: two stored answers with the same id are not deleted; the start stops unstamped and names the index."""
    from app import create_app
    from tests.conftest import _config

    folder = tmp_path / "legacy"
    folder.mkdir()
    _unstamped_without_answer_keys(folder, duplicates=True)
    with pytest.raises(migrations.SchemaMismatch, match="ix_response_uuid"):
        create_app(_config(folder))


@NO_FK
def test_0083_gives_every_member_an_account_and_goes_back(tmp_path):
    """konto-schluessel-plan.md (B1-B3): after 0083 every member and share of an ORCID iD points to an account, a
    pending one for an iD that never signed in, the rows of an account without ORCID to it, the entry of a deleted
    account to none; downgrade takes the pending accounts and the new columns out again, and up works once more."""
    import sqlalchemy as sa

    url, address = _fresh_url(tmp_path, "keys83")
    engine = create_engine(address)
    db.metadata.create_all(engine)
    with engine.connect() as connection:
        migrations._run_on(connection, command.stamp, "0085")  # the models have no key columns since 0085
        migrations._run_on(connection, command.downgrade, "0082")
        cols = {c["name"] for c in sa.inspect(connection).get_columns("design_share")}
        assert "user_id" not in cols and "pending" not in {c["name"] for c in sa.inspect(connection).get_columns("user")}
        t = db.metadata.tables
        user = sa.table("user", *(sa.column(c) for c in ("id", "orcid", "name", "affiliation", "is_admin", "created_at",
                                                          "experimental", "number")))
        from datetime import datetime

        now = datetime(2026, 10, 18, 12, 0)
        connection.execute(user.insert(), [
            {"id": 1, "orcid": "0000-0001-0000-0001", "name": "A", "affiliation": "", "is_admin": False,
             "created_at": now, "experimental": False, "number": 1},
            {"id": 2, "orcid": "q-local00000000000", "name": "L", "affiliation": "", "is_admin": False,
             "created_at": now, "experimental": False, "number": 2}])
        connection.execute(t["survey"].insert().values(id=1, title="S", created_by_id=1, code="1"))
        member = sa.table("survey_member", *(sa.column(c) for c in ("survey_id", "orcid", "user_id", "name", "affiliation",
                                                                     "position", "role", "created_at")))
        connection.execute(member.insert(), [
            {"survey_id": 1, "orcid": o, "user_id": u, "name": n, "affiliation": "", "position": 0, "role": "admin",
             "created_at": now} for o, u, n in (("0000-0001-0000-0001", 1, "A"), ("0000-0002-0000-0002", None, "B"),
                                                ("q-local00000000000", None, "L"), ("x-gone000000000000", None, "Weg"))])
        connection.execute(t["workgroup"].insert().values(id=1, name="G", created_by_id=1))
        group_member = sa.table("workgroup_member", *(sa.column(c) for c in ("group_id", "orcid", "name", "role",
                                                                            "created_at")))
        connection.execute(group_member.insert(), [
            {"group_id": 1, "orcid": "0000-0002-0000-0002", "name": "", "role": "member", "created_at": now},
            {"group_id": 1, "orcid": "0000-0003-0000-0003", "name": "C", "role": "member", "created_at": now}])
        connection.execute(t["design"].insert().values(id=1, name="D", owner_id=1))
        share = sa.table("design_share", sa.column("design_id"), sa.column("orcid"), sa.column("created_at"))
        connection.execute(share.insert(), [{"design_id": 1, "orcid": "0000-0002-0000-0002", "created_at": now},
                                            {"design_id": 1, "orcid": "q-local00000000000", "created_at": now}])
        connection.commit()

        migrations._run_on(connection, command.upgrade, "0083")
        accounts = dict(connection.execute(sa.text('SELECT orcid, id FROM "user"')).all())
        pending = dict(connection.execute(sa.text('SELECT orcid, pending FROM "user"')).all())
        assert set(accounts) == {"0000-0001-0000-0001", "q-local00000000000", "0000-0002-0000-0002", "0000-0003-0000-0003"}
        assert pending["0000-0002-0000-0002"] and pending["0000-0003-0000-0003"] and not pending["0000-0001-0000-0001"]
        assert connection.execute(sa.text('SELECT name FROM "user" WHERE orcid = \'0000-0003-0000-0003\'')).scalar() == "C"
        members = dict(connection.execute(sa.text("SELECT orcid, user_id FROM survey_member")).all())
        assert members == {"0000-0001-0000-0001": 1, "0000-0002-0000-0002": accounts["0000-0002-0000-0002"],
                           "q-local00000000000": 2, "x-gone000000000000": None}
        shares = dict(connection.execute(sa.text("SELECT orcid, user_id FROM design_share")).all())
        assert shares == {"0000-0002-0000-0002": accounts["0000-0002-0000-0002"], "q-local00000000000": 2}

        migrations._run_on(connection, command.downgrade, "0082")
        assert connection.execute(sa.text('SELECT count(*) FROM "user"')).scalar() == 2
        # the rows of whole accounts keep their account (the code before reads it as well), pending ones lose it
        assert connection.execute(sa.text("SELECT count(*) FROM survey_member WHERE user_id IS NOT NULL")).scalar() == 2
        migrations._run_on(connection, command.upgrade, "0083")
        assert connection.execute(sa.text('SELECT count(*) FROM "user"')).scalar() == 4
        migrations._run_on(connection, command.upgrade, "0085")
        assert migrations.differences(connection) == []
    engine.dispose()


@NO_FK
def test_0084_lets_the_keys_be_empty_and_goes_back(tmp_path):
    """konto-schluessel-2b-vorschlag.md (E2 A, first deploy): accounts without ORCID and the rows of people may have no
    key; downgrade gives every account and row a key again ("q-…" for an account without ORCID)."""
    import sqlalchemy as sa

    url, address = _fresh_url(tmp_path, "keys84")
    engine = create_engine(address)
    db.metadata.create_all(engine)
    with engine.connect() as connection:
        migrations._run_on(connection, command.stamp, "0085")
        migrations._run_on(connection, command.downgrade, "0084")
        user = sa.table("user", *(sa.column(c) for c in ("id", "orcid", "name", "affiliation", "is_admin", "created_at",
                                                          "experimental", "pending")))
        from datetime import datetime

        connection.execute(user.insert(), [{"id": 1, "orcid": None, "name": "Ohne", "affiliation": "", "is_admin": False,
                                            "created_at": datetime(2026, 10, 18), "experimental": False, "pending": False}])
        t = db.metadata.tables
        connection.execute(t["survey"].insert().values(id=1, title="S", created_by_id=1, code="1"))
        connection.execute(t["survey_member"].insert().values(survey_id=1, user_id=1, name="Ohne"))
        connection.commit()
        migrations._run_on(connection, command.downgrade, "0083")
        key = connection.execute(sa.text('SELECT orcid FROM "user" WHERE id = 1')).scalar()
        assert key.startswith("q-") and connection.execute(sa.text("SELECT orcid FROM survey_member")).scalar() == key
        migrations._run_on(connection, command.upgrade, "0085")
        assert migrations.differences(connection) == []
    engine.dispose()


def test_0085_turns_keys_into_accounts_and_drops_them(tmp_path):
    """konto-schluessel-2b-vorschlag.md (second deploy): consents and AInstein entries name accounts, an address book
    note and a project share of an iD without account get a pending account, the key columns and "q-…" keys go."""
    import json

    import sqlalchemy as sa

    url, address = _fresh_url(tmp_path, "keys85")
    engine = create_engine(address)
    db.metadata.create_all(engine)
    with engine.connect() as connection:
        migrations._run_on(connection, command.stamp, "0085")
        migrations._run_on(connection, command.downgrade, "0084")
        from datetime import datetime

        now = datetime(2026, 10, 18, 12, 0)
        user = sa.table("user", *(sa.column(c) for c in ("id", "orcid", "name", "affiliation", "is_admin", "created_at",
                                                          "experimental", "pending")))
        connection.execute(user.insert(), [
            {"id": 1, "orcid": "0000-0001-0000-0001", "name": "A", "affiliation": "", "is_admin": False,
             "created_at": now, "experimental": False, "pending": False},
            {"id": 2, "orcid": "q-local00000000000", "name": "L", "affiliation": "", "is_admin": False,
             "created_at": now, "experimental": False, "pending": False}])
        t = db.metadata.tables
        connection.execute(t["survey"].insert().values(id=1, title="S", created_by_id=1, code="1"))
        member = sa.table("survey_member", *(sa.column(c) for c in ("id", "survey_id", "orcid", "user_id", "name",
                                                                     "affiliation", "position", "role", "created_at")))
        connection.execute(member.insert(), [
            {"id": 1, "survey_id": 1, "orcid": "0000-0001-0000-0001", "user_id": 1, "name": "A", "affiliation": "",
             "position": 0, "role": "admin", "created_at": now},
            {"id": 2, "survey_id": 1, "orcid": "x-gone000000000000", "user_id": None, "name": "Weg", "affiliation": "",
             "position": 1, "role": "viewer", "created_at": now}])
        connection.execute(t["block"].insert().values(id=1, survey_id=1))
        connection.execute(t["item"].insert().values(id=1, block_id=1, type="consent", code="consent", config={
            "researchers": ["0000-0001-0000-0001", "u1", "x-gone000000000000", "q-nobody"]}))
        connection.execute(t["research_project"].insert().values(
            id=1, user_id=1, name="P",
            shares=[{"kind": "person", "orcid": "q-local00000000000", "name": "L", "role": "view"},
                    {"kind": "person", "orcid": "0000-0003-0000-0003", "name": "Neu", "role": "edit"}],
            comments=[{"id": "1", "orcid": "0000-0001-0000-0001", "name": "A", "text": "x"}]))
        contact = sa.table("contact", *(sa.column(c) for c in ("owner_id", "orcid", "user_id", "name", "last_used_at")))
        connection.execute(contact.insert(), [{"owner_id": 1, "orcid": "0000-0004-0000-0004", "user_id": None,
                                               "name": "Notiz", "last_used_at": now}])
        connection.commit()
        migrations._run_on(connection, command.upgrade, "0085")
        assert migrations.differences(connection) == []
        config = connection.execute(sa.text("SELECT config FROM item WHERE id = 1")).scalar()
        assert (json.loads(config) if isinstance(config, str) else config)["researchers"] == ["u1", "m2"]
        shares, comments = connection.execute(sa.text("SELECT shares, comments FROM research_project")).one()
        shares = json.loads(shares) if isinstance(shares, str) else shares
        new = connection.execute(sa.text("SELECT id FROM \"user\" WHERE orcid = '0000-0003-0000-0003'")).scalar()
        assert shares == [{"kind": "person", "name": "L", "role": "view", "user_id": 2},
                          {"kind": "person", "name": "Neu", "role": "edit", "user_id": new}]
        comments = json.loads(comments) if isinstance(comments, str) else comments
        assert comments == [{"id": "1", "name": "A", "text": "x", "user_id": 1}]
        note = connection.execute(sa.text("SELECT user_id FROM contact")).scalar()
        assert connection.execute(sa.text(f'SELECT pending FROM "user" WHERE id = {note}')).scalar()
        assert connection.execute(sa.text('SELECT orcid FROM "user" WHERE id = 2')).scalar() is None
    engine.dispose()


def test_0087_fills_live_extra_and_0088_drops_the_doubled_indexes(tmp_path):
    """Performance audit 2026-10-18, PERF-02 and PERF-10: 0087 sets the column live_extra from params for the further
    words of live votes (a questionnaire's params never make one) and puts it into ix_response_counts; 0088 drops
    ix_response_survey_id and ix_response_enrolment_id; both go back."""
    from datetime import datetime

    import sqlalchemy as sa

    url, address = _fresh_url(tmp_path, "live87")
    engine = create_engine(address)
    db.metadata.create_all(engine)
    with engine.connect() as connection:
        migrations._run_on(connection, command.stamp, "0088")
        migrations._run_on(connection, command.downgrade, "0085")
        indexes = {i["name"]: i["column_names"] for i in sa.inspect(connection).get_indexes("response")}
        assert "ix_response_survey_id" in indexes and "live_extra" not in indexes["ix_response_counts"]
        t = db.metadata.tables
        connection.execute(sa.text('INSERT INTO "user" (id, name, affiliation, is_admin, created_at, experimental, '
                                   "pending) VALUES (1, 'A', '', false, :t, false, false)"), {"t": datetime(2026, 10, 18)})
        connection.execute(t["survey"].insert().values(id=1, title="Live", created_by_id=1, code="1",
                                                       live={"run": {"id": 1}}))
        connection.execute(t["survey"].insert().values(id=2, title="Bogen", created_by_id=1, code="2"))
        rows = [(1, 1, {"live_run": 1}), (2, 1, {"live_run": 1, "live_extra": True}), (3, 2, {"live_extra": True}),
                (4, 2, None)]
        response = sa.table("response", *(sa.column(c) for c in ("id", "survey_id", "language", "source", "status",
                                                                  "answers", "started_at", "params")))
        connection.execute(response.insert(), [
            {"id": i, "survey_id": s, "language": "de", "source": "real", "status": "completed", "answers": "{}",
             "started_at": datetime(2026, 10, 18), "params": None if p is None else __import__("json").dumps(p)}
            for i, s, p in rows])
        connection.commit()
        migrations._run_on(connection, command.upgrade, "0088")
        assert connection.execute(sa.text("SELECT id FROM response WHERE live_extra ORDER BY id")).scalars().all() == [2]
        indexes = {i["name"]: i["column_names"] for i in sa.inspect(connection).get_indexes("response")}
        assert indexes["ix_response_counts"][-1] == "live_extra"
        assert "ix_response_survey_id" not in indexes and "ix_response_enrolment_id" not in indexes
        assert migrations.differences(connection) == []
        migrations._run_on(connection, command.downgrade, "0085")
        indexes = {i["name"] for i in sa.inspect(connection).get_indexes("response")}
        assert {"ix_response_survey_id", "ix_response_enrolment_id"} <= indexes
        assert "live_extra" not in {c["name"] for c in sa.inspect(connection).get_columns("response")}
        migrations._run_on(connection, command.upgrade, "0088")
    engine.dispose()


_ENV = '''from alembic import context
connection = context.config.attributes["connection"]
context.configure(connection=connection, target_metadata=None, render_as_batch=True, transaction_per_migration=True)
with context.begin_transaction():
    context.run_migrations()
'''


def _script_dir(folder, versions):
    """A small Alembic script folder with the env of migrations/env.py's kind and these {name: upgrade body}."""
    from alembic.config import Config

    (folder / "versions").mkdir(parents=True)
    (folder / "env.py").write_text(_ENV, encoding="utf-8")
    down = None
    for name, body in versions.items():
        (folder / "versions" / f"{name}.py").write_text(
            "from alembic import op\nimport sqlalchemy as sa\n"
            f"revision = {name!r}\ndown_revision = {down!r}\nbranch_labels = None\ndepends_on = None\n"
            f"def upgrade():\n{body}\ndef downgrade():\n    pass\n", encoding="utf-8")
        down = name

    def config(connection=None):
        cfg = Config()
        cfg.set_main_option("script_location", str(folder))
        if connection is not None:
            cfg.attributes["connection"] = connection
        return cfg
    return config


def test_a_migration_failing_in_the_middle_leaves_nothing_on_sqlite(tmp_path, monkeypatch):
    """Audit 2026-10-21, B15: on SQLite the ALTER TABLE of a migration ran outside its transaction (Python's sqlite3
    begins one only before DML), so a migration failing in the middle kept its first steps while the version stayed,
    and the next start skipped the rest. The migrations' connection has real transactions now."""
    config = _script_dir(tmp_path / "s", {
        "a1": "    op.create_table('t', sa.Column('id', sa.Integer, primary_key=True), sa.Column('v', sa.Integer))\n"
              "    op.execute('INSERT INTO t (v) VALUES (1)')\n",
        "a2": "    op.add_column('t', sa.Column('code', sa.String(8), nullable=True))\n"
              "    op.execute(\"UPDATE t SET code = 'x'\")\n    raise RuntimeError('fails in the middle')\n"})
    monkeypatch.setattr(migrations, "_config", config)
    engine = create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    with engine.connect() as connection:
        migrations._run_on(connection, command.upgrade, "a1")
    with engine.connect() as connection:
        with pytest.raises(RuntimeError, match="in the middle"):
            migrations._run_on(connection, command.upgrade, "head")
    with engine.connect() as connection:
        assert migrations.current(connection) == "a1"
        assert [r[1] for r in connection.exec_driver_sql("PRAGMA table_info(t)")] == ["id", "v"]
        assert connection.exec_driver_sql("SELECT v FROM t").all() == [(1,)]
        assert connection.connection.dbapi_connection.isolation_level == ""  # the pool gets it back as it was
    engine.dispose()


def test_an_index_built_concurrently_runs_on_sqlite_too(tmp_path, monkeypatch):
    """The rule of tests/platform/test_rolling_restart.py for indexes on large tables (B13): postgresql_concurrently
    inside autocommit_block() has to run on SQLite as well, also with the migrations' transactions of B15."""
    config = _script_dir(tmp_path / "s", {
        "b1": "    op.create_table('response', sa.Column('id', sa.Integer, primary_key=True), sa.Column('x', sa.Integer))\n",
        "b2": "    with op.get_context().autocommit_block():\n"
              "        op.create_index('ix_response_x', 'response', ['x'], postgresql_concurrently=True)\n",
        "b3": "    op.add_column('response', sa.Column('y', sa.Integer, nullable=True))\n"})
    monkeypatch.setattr(migrations, "_config", config)
    engine = create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    with engine.connect() as connection:
        migrations._run_on(connection, command.upgrade, "head")
    with engine.connect() as connection:
        assert migrations.current(connection) == "b3"
        assert "ix_response_x" in {r[1] for r in connection.exec_driver_sql("PRAGMA index_list(response)")}
        assert "y" in {r[1] for r in connection.exec_driver_sql("PRAGMA table_info(response)")}
    engine.dispose()


def _old_schema_without_unique(url, table, columns):
    """_old_schema, but the table lacks the UNIQUE over these columns (a table add_missing_columns or an old
    create_all made without it)."""
    from sqlalchemy import UniqueConstraint

    old = MetaData()
    for t in db.metadata.sorted_tables:
        t.to_metadata(old)
    copy = old.tables[table]
    for c in [c for c in copy.constraints if isinstance(c, UniqueConstraint) and {x.name for x in c.columns} == set(columns)]:
        copy.constraints.discard(c)
    engine = create_engine(url)
    old.create_all(engine)
    engine.dispose()


@needs_sqlite
def test_stamping_creates_missing_unique_constraints_after_a_copy(tmp_path):
    """Audit 2026-10-21, B16: a database without alembic_version was stamped without the UNIQUE constraints of the
    models (only listed) and without a copy before add_missing_columns and create_all changed it."""
    from app import create_app

    _, address = _fresh_url(tmp_path, "uq")
    _old_schema_without_unique(address, "diary_invite", ("survey_id", "code"))
    app = create_app(_config(tmp_path))
    with app.app_context():
        assert migrations.current() == migrations.head()
        engine = db.engine
        with engine.connect() as connection:
            uniques = [set(i["column_names"]) for i in sqlalchemy_inspect(connection).get_indexes("diary_invite")
                       if i.get("unique")]
        assert {"survey_id", "code"} in uniques
        assert any(b["name"].endswith("-vor-migration.db") for b in migrations.backups(app))
    _dispose(app)


@needs_sqlite
def test_stamping_stops_on_rows_that_break_a_unique_constraint(tmp_path):
    from app import create_app

    _, address = _fresh_url(tmp_path, "uq")
    _old_schema_without_unique(address, "diary_invite", ("survey_id", "code"))
    con = sqlite3.connect(tmp_path / "test.db")
    con.execute("INSERT INTO diary_invite (survey_id, code, created_at) VALUES (1, 'abc', '2026-10-01'), "
                "(1, 'abc', '2026-10-02')")
    con.commit()
    con.close()
    with pytest.raises(migrations.SchemaMismatch, match="diary_invite_survey_id_code_key"):
        create_app(_config(tmp_path))
    con = sqlite3.connect(tmp_path / "test.db")
    assert con.execute("SELECT count(*) FROM diary_invite").fetchone() == (2,)  # nothing deleted
    assert con.execute("SELECT name FROM sqlite_master WHERE name = 'alembic_version'").fetchall() == []
    con.close()
